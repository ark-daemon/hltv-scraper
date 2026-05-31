"""
scrapers/rankings.py — Scrape current and all historical weekly world team rankings.
"""

from datetime import datetime, timedelta, timezone

from loguru import logger

from bs4 import BeautifulSoup

from config import BASE_URL
from scrapers.base import BaseScraper

# HLTV started publishing weekly rankings on this Monday
RANKINGS_START_DATE = datetime(2012, 9, 3)

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


class RankingsScraper(BaseScraper):
    """
    Step 1: Scrape the current world ranking snapshot.
    Step 2: Iterate all Mondays from 2012-09-03 to today and scrape each.
    """

    SCRAPER_KEY = "rankings"

    async def run(self) -> dict:
        logger.info(f"[{self.name}] Starting...")
        stats = {"processed": 0, "inserted": 0, "skipped": 0, "errors": 0}

        # Step 1 — Current ranking
        current_rows = await self._scrape_ranking_page(
            f"{BASE_URL}/ranking/teams",
            snapshot_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        if current_rows:
            n = await self.db.bulk_insert("world_rankings", current_rows, replace=True)
            stats["inserted"] += n
            stats["processed"] += 1

        # Step 2 — Historical weekly snapshots
        all_mondays = list(self._generate_mondays())
        done_set = self.checkpoint.get_done_set(self.SCRAPER_KEY)
        total = len(all_mondays)
        logger.info(f"[{self.name}] {total} historical weekly snapshots to process.")

        for i, dt in enumerate(all_mondays):
            date_key = dt.strftime("%Y-%m-%d")
            if date_key in done_set:
                stats["skipped"] += 1
                continue

            month_name = MONTH_NAMES[dt.month - 1]
            day = str(dt.day)  # no zero-padding
            url = f"{BASE_URL}/ranking/teams/{dt.year}/{month_name}/{day}"

            rank_rows = await self._scrape_ranking_page(url, snapshot_date=date_key)
            if rank_rows is None:
                stats["errors"] += 1
                continue  # Do NOT mark done; will retry next run
            if rank_rows:
                n = await self.db.bulk_insert("world_rankings", rank_rows, replace=True)
                stats["inserted"] += n
            self.checkpoint.mark_done(self.SCRAPER_KEY, date_key)
            stats["processed"] += 1

            if (i + 1) % 20 == 0:
                self.log_progress(i + 1, total)

        self.checkpoint.save()
        logger.info(
            f"[{self.name}] Done. processed={stats['processed']} inserted={stats['inserted']} "
            f"skipped={stats['skipped']} errors={stats['errors']}"
        )
        return stats

    # ------------------------------------------------------------------
    # Scraping
    # ------------------------------------------------------------------

    async def _scrape_ranking_page(self, url: str, snapshot_date: str) -> list[dict] | None:
        """Fetch a ranking page and return parsed rows, or None on fetch failure."""
        soup = await self.fetch(url)
        if soup is None:
            return None
        return self._parse_ranking_page(soup, snapshot_date)

    def _parse_ranking_page(self, soup: BeautifulSoup, snapshot_date: str) -> list[dict]:
        """Extract team ranking rows from a ranking page."""
        rows = []

        for team_el in soup.select("div.ranked-team, div.rankings-team"):
            try:
                # Rank number
                rank_el = team_el.select_one("span.position, div.ranking-position")
                rank = self.parse_int(self.safe_text(rank_el))

                # Team name and ID
                team_link = team_el.select_one("a.name, a[href*='/team/']")
                team_name = self.safe_text(team_link)
                team_id = None
                if team_link:
                    team_id = self.extract_id_from_url(team_link.get("href", ""), -2)

                # Points
                points_el = team_el.select_one("span.points")
                points_text = self.safe_text(points_el) or ""
                points = self.parse_int(points_text.replace("pts", "").strip())

                # Rank change (positive = up, negative = down)
                change_el = team_el.select_one("div.change, span.change")
                rank_change = None
                if change_el:
                    change_text = self.safe_text(change_el) or "0"
                    rank_change = self.parse_int(change_text)
                    # Check direction class
                    classes = change_el.get("class") or []
                    if (
                        any("down" in c for c in classes)
                        and rank_change
                        and rank_change > 0
                    ):
                        rank_change = -rank_change

                if rank and team_name:
                    rows.append(
                        {
                            "snapshot_date": snapshot_date,
                            "rank": rank,
                            "team_id": team_id,
                            "team_name": team_name,
                            "points": points,
                            "rank_change": rank_change,
                        }
                    )
            except Exception as e:
                logger.debug(f"[{self.name}] Error parsing ranking row: {e}")

        return rows

    # ------------------------------------------------------------------
    # Date generation
    # ------------------------------------------------------------------

    def _generate_mondays(self):
        """Yield all Mondays from RANKINGS_START_DATE to today."""
        current = RANKINGS_START_DATE
        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        while current <= today:
            yield current
            current += timedelta(weeks=1)
