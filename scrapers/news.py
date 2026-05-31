"""
scrapers/news.py — Scrape all news articles from HLTV's monthly archive.
"""

import re
from datetime import datetime, timezone

from loguru import logger

from bs4 import BeautifulSoup

from config import BASE_URL
from scrapers.base import BaseScraper

START_YEAR = 2012

MONTH_NAMES = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


class NewsScraper(BaseScraper):
    """
    Iterates every year/month from 2012 to today and scrapes
    all news articles from each monthly archive page.
    """

    SCRAPER_KEY = "news"

    async def run(self) -> dict:
        logger.info(f"[{self.name}] Starting...")
        stats = {"processed": 0, "inserted": 0, "skipped": 0, "errors": 0}

        now = datetime.now(timezone.utc)
        done_set = self.checkpoint.get_done_set(self.SCRAPER_KEY)

        for year in range(START_YEAR, now.year + 1):
            for month in range(1, 13):
                # Skip future months
                if year == now.year and month > now.month:
                    break

                month_key = f"{year}-{month:02d}"
                if month_key in done_set:
                    stats["skipped"] += 1
                    continue

                month_name = MONTH_NAMES[month - 1]
                url = f"{BASE_URL}/news/archive/{year}/{month_name}"

                soup = await self.fetch(url)
                if soup is None:
                    stats["errors"] += 1
                    logger.warning(
                        f"[{self.name}] Failed to fetch month {month_key}; leaving pending for retry."
                    )
                    continue

                articles = self._parse_news_archive(soup)
                if articles:
                    n = await self.db.bulk_insert("news", articles)
                    stats["inserted"] += n
                    logger.debug(
                        f"[{self.name}] {month_key}: {len(articles)} articles found, {n} inserted."
                    )

                self.checkpoint.mark_done(self.SCRAPER_KEY, month_key)
                stats["processed"] += 1

        self.checkpoint.save()
        logger.info(
            f"[{self.name}] Done. processed={stats['processed']} inserted={stats['inserted']} "
            f"skipped={stats['skipped']} errors={stats['errors']}"
        )
        return stats

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_news_archive(self, soup: BeautifulSoup) -> list[dict]:
        """Parse all news article rows from a monthly archive page."""
        articles = []

        # HLTV archive uses div.article or similar containers
        for el in soup.select(
            "div.article, div.newsline, a.newsline, div.standard-box.article"
        ):
            article = self._parse_article_element(el)
            if article:
                articles.append(article)

        return articles

    def _parse_article_element(self, el) -> dict | None:
        """Extract fields from a single article element."""
        try:
            # News ID from link href
            link = el if el.name == "a" else el.select_one("a[href*='/news/']")
            if not link:
                return None
            href = link.get("href", "")
            news_id = self._extract_news_id(href)
            if not news_id:
                return None

            # Title
            title_el = el.select_one("div.title, a.newsline, span.newsline-title")
            if not title_el and el.name == "a":
                title_el = el
            title = self.safe_text(title_el)
            if not title and el.name == "a":
                title = self.safe_text(el)

            # Date
            date_el = el.select_one("div.date, span.date, time")
            date_text = self.safe_text(date_el)
            date_str = self._parse_date(date_text)

            # Author
            author_el = el.select_one("span.author, div.author, a.author")
            author = self.safe_text(author_el)

            # Category / tag
            cat_el = el.select_one("span.category, div.category, span.tag")
            category = self.safe_text(cat_el)

            # Related IDs (team, player, event)
            related_team_id = None
            related_player_id = None
            related_event_id = None

            for a in el.select("a[href*='/team/']"):
                related_team_id = self.extract_id_from_url(a.get("href", ""), -2)
                if related_team_id:
                    break
            for a in el.select("a[href*='/player/']"):
                related_player_id = self.extract_id_from_url(a.get("href", ""), -2)
                if related_player_id:
                    break
            for a in el.select("a[href*='/events/']"):
                related_event_id = self.extract_id_from_url(a.get("href", ""), -2)
                if related_event_id:
                    break

            return {
                "news_id": news_id,
                "title": title,
                "date": date_str,
                "author": author,
                "category": category,
                "hltv_url": BASE_URL + href if href.startswith("/") else href,
                "related_team_id": related_team_id,
                "related_player_id": related_player_id,
                "related_event_id": related_event_id,
                "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        except Exception as e:
            logger.debug(f"[{self.name}] Error parsing article element: {e}")
            return None

    def _extract_news_id(self, href: str) -> int | None:
        """
        Extract news ID from /news/{id}/{slug} or /news/{id}-{slug}.
        """
        if not href:
            return None
        # Try /news/12345/title-slug
        match = re.search(r"/news/(\d+)/", href)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        # Try /news/12345-title-slug
        match = re.search(r"/news/(\d+)-", href)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        return None

    def _parse_date(self, text: str | None) -> str | None:
        """Parse various date formats to ISO 8601."""
        if not text:
            return None
        # Try standard ISO
        match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        if match:
            return match.group(1)
        # Try "Jan 15, 2024" style
        try:
            from dateutil import parser as dp

            return dp.parse(text, fuzzy=True).strftime("%Y-%m-%d")
        except Exception:
            pass
        return None
