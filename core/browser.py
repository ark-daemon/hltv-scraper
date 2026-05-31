"""
core/browser.py — BrowserManager for resilient, human-like page fetching.

Handles automated navigation, challenge-page detection, retries, and
automatic session restarts every 200 requests to maintain stable access.
"""

import asyncio
import sys

from bs4 import BeautifulSoup
from loguru import logger

from config import (
    BROWSER_BACKEND,
    BROWSER_GEOIP,
    BROWSER_HUMAN_PRESET,
    BROWSER_PROXY,
    BROWSER_RETRY_WAIT,
    BROWSER_RESTART_EVERY,
    HEADLESS,
    MAX_RETRIES,
    PAGE_LOAD_TIMEOUT,
)
from core.rate_limiter import rate_limiter


class AccessBlockedError(Exception):
    """Raised when an access challenge cannot be resolved after retries."""

    pass


class BrowserManager:
    """
    Manages a stealth browser session for resilient, human-like page fetching.

    - Rotates user agents per session
    - Detects and waits out access-challenge pages
    - Restarts session every 200 requests to rotate fingerprint
    - Retries failed pages with exponential backoff
    """

    def __init__(self) -> None:
        self._browser = None
        self._page = None
        self._provider_ctx = None
        self._session_requests: int = 0
        self._launched: bool = False

    async def launch(self) -> None:
        """Launch stealth browser with a fresh user agent."""
        try:
            backend = BROWSER_BACKEND.lower()
            logger.info(f"[Browser] Launching {backend} (headless={HEADLESS})...")
            if backend == "cloakbrowser":
                self._browser = await self._launch_cloakbrowser()
            elif backend == "camoufox":
                self._browser = await self._launch_camoufox()
            else:
                raise ValueError(f"Unsupported browser backend: {BROWSER_BACKEND}")
            self._page = await self._browser.new_page()
            self._session_requests = 0
            self._launched = True
            logger.info(f"[Browser] {backend} launched successfully.")
        except Exception as e:
            logger.error(f"[Browser] Failed to launch {BROWSER_BACKEND}: {e}")
            raise

    async def _launch_cloakbrowser(self):
        from cloakbrowser import launch_async

        launch_kwargs = {
            "headless": HEADLESS,
            "humanize": True,
            "human_preset": BROWSER_HUMAN_PRESET,
        }
        if BROWSER_PROXY:
            launch_kwargs["proxy"] = BROWSER_PROXY
        if BROWSER_GEOIP:
            launch_kwargs["geoip"] = True
        return await launch_async(**launch_kwargs)

    async def _launch_camoufox(self):
        from camoufox.async_api import AsyncCamoufox

        self._provider_ctx = AsyncCamoufox(headless=HEADLESS, humanize=True)
        return await self._provider_ctx.__aenter__()

    async def close(self) -> None:
        """Cleanly shut down the browser session."""
        try:
            if self._page:
                await self._page.close()
            if self._provider_ctx:
                # Pass active exception info so the context manager can
                # handle cleanup correctly (e.g. kill leaked subprocesses).
                exc_info = sys.exc_info()
                await self._provider_ctx.__aexit__(*exc_info)
            elif self._browser:
                await self._browser.close()
            self._page = None
            self._browser = None
            self._provider_ctx = None
            self._launched = False
            logger.info("[Browser] Session closed.")
        except Exception as e:
            logger.warning(f"[Browser] Error during close: {e}")

    async def restart(self) -> None:
        """Close and reopen a fresh browser session to rotate fingerprint."""
        logger.info("[Browser] Restarting session to rotate fingerprint...")
        await self.close()
        await asyncio.sleep(3)
        await self.launch()
        logger.info("[Browser] Session restarted.")

    async def fetch_page(self, url: str) -> BeautifulSoup | None:
        """
        Fetch a URL and return a BeautifulSoup object.

        Steps:
          1. Call rate_limiter.wait() — ALWAYS, no exceptions
          2. Navigate with stealth browser
          3. Wait for document.readyState == 'complete'
          4. Detect and wait out access-challenge pages
          5. Return BeautifulSoup or None after MAX_RETRIES exhausted

        Restarts browser session every RESTART_EVERY requests.
        """
        if not self._launched:
            await self.launch()

        # Restart session every N requests to rotate fingerprint
        if self._session_requests >= BROWSER_RESTART_EVERY:
            await self.restart()

        for attempt in range(MAX_RETRIES):
            try:
                # ALWAYS wait before fetching - no exceptions
                await rate_limiter.wait()

                self._session_requests += 1

                logger.debug(f"[Browser] Fetching: {url} (attempt {attempt + 1})")
                response = await self._page.goto(url, timeout=PAGE_LOAD_TIMEOUT * 1000)

                # Treat rate-limit and server-side blocks as retryable failures
                if response is not None and response.status in (429, 403, 503):
                    logger.warning(
                        f"[Browser] HTTP {response.status} on {url} "
                        f"(attempt {attempt + 1}/{MAX_RETRIES}) — backing off."
                    )
                    await rate_limiter.backoff(attempt)
                    continue

                # Wait for full page load
                await self._wait_for_ready()

                # Check for access-challenge page
                if await self._is_challenge_page():
                    await self._resolve_challenge(url, attempt)

                html = await self._page.content()
                soup = BeautifulSoup(html, "lxml")
                return soup

            except AccessBlockedError:
                logger.error(f"[Browser] Access blocked on {url} - backing off.")
                await rate_limiter.backoff(attempt)
                if attempt < MAX_RETRIES - 1:
                    await self.restart()
                continue

            except Exception as e:
                logger.warning(
                    f"[Browser] Error fetching {url} (attempt {attempt + 1}/{MAX_RETRIES}): {e}"
                )
                await rate_limiter.backoff(attempt)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(BROWSER_RETRY_WAIT)
                    try:
                        await self.restart()
                    except Exception as exc:
                        logger.warning(
                            f"[Browser] Restart failed: {exc}", exc_info=True
                        )
                        # Force full re-launch on next attempt
                        self._launched = False
                        self._page = None
                        self._browser = None
                        self._provider_ctx = None
                continue

        logger.error(
            f"[Browser] Exhausted {MAX_RETRIES} retries for {url}. Returning None."
        )
        return None

    async def _wait_for_ready(self) -> None:
        """Wait until document.readyState is 'complete'."""
        try:
            await self._page.wait_for_function(
                "document.readyState === 'complete'",
                timeout=PAGE_LOAD_TIMEOUT * 1000,
            )
        except Exception:
            # Fallback: just wait a moment if the function check fails
            await asyncio.sleep(3)

    async def _is_challenge_page(self) -> bool:
        """Check if the current page is an access-challenge interstitial."""
        try:
            title = await self._page.title()
            return "Just a moment" in title or "Attention Required" in title
        except Exception:
            return False

    async def _resolve_challenge(self, url: str, outer_attempt: int) -> None:
        """
        Wait for an access-challenge page to resolve.
        Checks up to 3 times, 8 seconds apart.
        Raises AccessBlockedError if unresolved after all checks.
        """
        logger.warning(f"[Browser] Access challenge detected on {url}. Waiting...")
        for check in range(3):
            await asyncio.sleep(8)
            await self._wait_for_ready()
            if not await self._is_challenge_page():
                logger.info("[Browser] Access challenge resolved.")
                return
            logger.warning(
                f"[Browser] Still on access-challenge page (check {check + 1}/3)..."
            )

        raise AccessBlockedError(
            f"Access challenge not resolved after 3 attempts on {url}"
        )
