"""
core/rate_limiter.py - Human-like rate limiter singleton.

Every single HTTP request must call rate_limiter.wait() before fetching.
No bypasses. No fast paths. Always sleeps.
"""

import asyncio
import random
import threading
import time

from loguru import logger

from config import (
    BACKOFF_MAX,
    BACKOFF_START,
    BATCH_PAUSE_EVERY,
    BATCH_PAUSE_MAX,
    BATCH_PAUSE_MIN,
    MAX_DELAY,
    MIN_DELAY,
)


class RateLimiter:
    """
    Singleton rate limiter that enforces human-like delays between every request.

    Usage:
        await rate_limiter.wait()   # BEFORE every fetch
        await rate_limiter.backoff(attempt)  # AFTER a 429/403/5xx
    """

    _instance: "RateLimiter | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "RateLimiter":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._request_count: int = 0
        self._batch_count: int = 0
        self._initialized = True
        logger.debug("[Rate Limiter] Initialized.")

    async def wait(self) -> None:
        """
        Must be called before EVERY single HTTP request.
        Sleeps MIN_DELAY–MAX_DELAY seconds.
        Every BATCH_PAUSE_EVERY requests: additional BATCH_PAUSE_MIN–MAX sleep.
        """
        self._request_count += 1
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        logger.debug(
            f"[Rate Limiter] Request #{self._request_count} - sleeping {delay:.1f}s"
        )
        await asyncio.sleep(delay)

        # Batch pause
        if self._request_count % BATCH_PAUSE_EVERY == 0:
            self._batch_count += 1
            batch_delay = random.uniform(BATCH_PAUSE_MIN, BATCH_PAUSE_MAX)
            logger.info(
                f"[Rate Limiter] Batch pause #{self._batch_count} - sleeping {batch_delay:.1f}s"
            )
            await asyncio.sleep(batch_delay)

    async def backoff(self, attempt: int) -> None:
        """
        Called after a 429/403/5xx response.
        Sleeps: min(BACKOFF_START * 2^attempt, BACKOFF_MAX) seconds.
        """
        sleep_time = min(BACKOFF_START * (2**attempt), BACKOFF_MAX)
        sleep_time *= random.uniform(0.75, 1.25)
        logger.warning(
            f"[Rate Limiter] Backoff attempt {attempt} - sleeping {sleep_time:.1f}s"
        )
        await asyncio.sleep(sleep_time)

    @property
    def request_count(self) -> int:
        return self._request_count


# Singleton instance - import and use this everywhere
rate_limiter = RateLimiter()
