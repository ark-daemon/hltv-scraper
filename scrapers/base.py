"""
scrapers/base.py - Abstract base class for all HLTV scrapers.

Provides shared utilities: fetch, ID extraction, safe parsing helpers,
progress logging, and checkpoint/DB integration.
"""

import re
from abc import ABC, abstractmethod
from bs4 import BeautifulSoup, Tag
from loguru import logger

from config import BASE_URL
from core.rate_limiter import rate_limiter


class BaseScraper(ABC):
    """
    Abstract base for all scrapers. Subclasses must implement run().

    Constructor args:
        db         - Database instance
        browser    - BrowserManager instance
        checkpoint - Checkpoint instance
    """

    def __init__(self, db, browser, checkpoint) -> None:
        self.db = db
        self.browser = browser
        self.checkpoint = checkpoint
        self.rate_limiter = rate_limiter
        self.name = self.__class__.__name__

    # ------------------------------------------------------------------
    # Page fetching
    # ------------------------------------------------------------------

    async def fetch(self, url: str) -> BeautifulSoup | None:
        """Fetch a URL and return BeautifulSoup, or None on failure."""
        # Ensure full URL
        if url.startswith("/"):
            url = BASE_URL + url
        result = await self.browser.fetch_page(url)
        if result is None:
            logger.warning(f"[{self.name}] fetch returned None for {url}")
        return result

    # ------------------------------------------------------------------
    # ID extraction helpers
    # ------------------------------------------------------------------

    def extract_id_from_url_regex(self, url: str | None, pattern: str) -> int | None:
        """Extract an integer ID from *url* using a regex with one capture group."""
        if not url:
            return None
        match = re.search(pattern, url)
        if not match:
            return None
        try:
            return int(match.group(1))
        except (TypeError, ValueError, IndexError):
            return None

    def extract_id_from_url(self, url: str, position: int = -2) -> int | None:
        """
        Extract integer ID from a URL by splitting on '/'.

        Examples:
          /matches/2370727/faze-vs-navi  → position=-2 → 2370727
          /player/7998/zywoo             → position=-2 → 7998
          /team/6651/navi                → position=-2 → 6651
        """
        if not url:
            return None
        parts = [p for p in url.split("/") if p]
        try:
            idx = position if position >= 0 else len(parts) + position
            return int(parts[idx])
        except (IndexError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Safe element reading
    # ------------------------------------------------------------------

    def safe_text(self, element: Tag | None) -> str | None:
        """Return stripped text content or None."""
        if element is None:
            return None
        return element.get_text(strip=True) or None

    def safe_attr(self, element: Tag | None, attr: str) -> str | None:
        """Return an attribute value or None."""
        if element is None:
            return None
        return element.get(attr) or None

    # ------------------------------------------------------------------
    # Type-safe parsers
    # ------------------------------------------------------------------

    def parse_int(self, text: str | None) -> int | None:
        """Strip non-numeric chars and return int, or None."""
        if text is None:
            return None
        cleaned = re.sub(r"[^\d\-]", "", str(text).strip())
        if not cleaned or cleaned == "-":
            return None
        try:
            return int(cleaned)
        except ValueError:
            return None

    def parse_float(self, text: str | None) -> float | None:
        """Strip non-numeric chars except '.' and '-', return float or None."""
        if text is None:
            return None
        raw = str(text).strip()
        sign = "-" if raw.startswith("-") else ""
        unsigned = raw[1:] if sign else raw
        cleaned = sign + re.sub(r"[^\d.]", "", unsigned)
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None

    def parse_percent(self, text: str | None) -> float | None:
        """Strip '%' and return float, or None."""
        if text is None:
            return None
        return self.parse_float(str(text).replace("%", "").strip())

    def parse_prize_usd(self, text: str | None) -> int | None:
        """Parse prize strings like '$500,000' → 500000."""
        if not text:
            return None
        cleaned = re.sub(r"[^\d]", "", str(text))
        try:
            return int(cleaned) if cleaned else None
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Progress logging
    # ------------------------------------------------------------------

    def log_progress(self, done: int, total: int, extra: str = "") -> None:
        """Log scraper progress: [ScraperName] done/total (pct%)."""
        if total > 0:
            pct = (done / total) * 100
            msg = f"[{self.name}] {done}/{total} ({pct:.1f}%)"
        else:
            msg = f"[{self.name}] {done} processed"
        if extra:
            msg += f" - {extra}"
        logger.info(msg)

    # ------------------------------------------------------------------
    # Abstract run
    # ------------------------------------------------------------------

    @abstractmethod
    async def run(self) -> dict:
        """
        Execute the scraper. Must return a summary dict:
        {
            "processed": int,
            "inserted": int,
            "skipped": int,
            "errors": int,
        }
        """
        ...
