"""
scrapers/event_detail.py - Scrape team placements and prizes for each event.
"""

import json
import os
import re
from loguru import logger

from config import BASE_URL, CHECKPOINT_DIR
from scrapers.base import BaseScraper


class EventDetailScraper(BaseScraper):
    """
    For each event, scrapes the event detail page to extract:
      - Participating teams, placements, prizes, qualification paths
    Inserts into event_teams table.
    """

    SCRAPER_KEY = "event_detail"

    async def run(self) -> dict:
        logger.info(f"[{self.name}] Starting...")
        stats = {"processed": 0, "inserted": 0, "skipped": 0, "errors": 0}

        event_ids = await self._load_event_ids()
        if not event_ids:
            logger.warning(
                f"[{self.name}] No event IDs found in DB or legacy checkpoint."
            )
            return stats

        existing_event_ids = await self.db.get_all_ids("event_teams", "event_id")
        done_set = self.checkpoint.get_done_set(self.SCRAPER_KEY)
        total = len(event_ids)
        logger.info(f"[{self.name}] Processing {total} events.")

        for i, event_id in enumerate(event_ids):
            if event_id in existing_event_ids or str(event_id) in done_set:
                stats["skipped"] += 1
                continue

            # Get slug from events table
            rows = await self.db.execute(
                "SELECT hltv_url FROM events WHERE event_id = ?", [event_id]
            )
            if rows and rows[0][0]:
                url = rows[0][0]
            else:
                url = f"{BASE_URL}/events/{event_id}/event"

            soup = await self.fetch(url)
            if soup is None:
                stats["errors"] += 1
                continue

            team_rows = self._parse_event_teams(soup, event_id)
            if team_rows:
                n = await self.db.bulk_insert("event_teams", team_rows, replace=True)
                stats["inserted"] += n

            self.checkpoint.mark_done(self.SCRAPER_KEY, event_id)
            stats["processed"] += 1

            if (i + 1) % 50 == 0:
                self.log_progress(i + 1, total)

        self.checkpoint.save()
        logger.info(
            f"[{self.name}] Done. processed={stats['processed']} inserted={stats['inserted']} "
            f"skipped={stats['skipped']} errors={stats['errors']}"
        )
        return stats

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_event_teams(self, soup, event_id: int) -> list[dict]:
        """Extract team placement rows from an event detail page."""
        rows = []

        # Placements section - various layouts depending on event type
        placement_els = soup.select(
            "div.placements div.placement, "
            "table.placements tbody tr, "
            "div.event-team-container, "
            "div.team-box"
        )

        for el in placement_els:
            try:
                team_link = el.select_one("a[href*='/team/']")
                if not team_link:
                    continue
                href = team_link.get("href", "")
                team_id = self.extract_id_from_url(href, -2)
                team_name = self.safe_text(team_link)

                # Placement
                place_el = el.select_one("div.placement-text, span.place, td.placement")
                place_text = self.safe_text(place_el) or ""
                placement = self._parse_placement(place_text)

                # Prize
                prize_el = el.select_one("div.prize, span.prize, td.prize")
                prize_raw = self.safe_text(prize_el)
                prize_usd = self.parse_prize_usd(prize_raw)

                # Qualification path
                qual_el = el.select_one("div.qualifier, span.qualifier-type")
                qualified_via = self.safe_text(qual_el)

                # Skip sparse placeholder rows: require real placement or prize signal.
                if placement is None and not prize_raw and prize_usd is None:
                    continue

                rows.append({
                    "event_id": event_id,
                    "team_id": team_id,
                    "team_name": team_name,
                    "placement": placement,
                    "prize": prize_raw,
                    "prize_usd": prize_usd,
                    "qualified_via": qualified_via,
                })
            except Exception as e:
                logger.debug(f"[{self.name}] Error parsing event team entry: {e}")

        return rows

    def _parse_placement(self, text: str) -> int | None:
        """
        Parse placement strings like '1st', '2nd', '3-4th', '5-8th' -> integer.
        Returns the best (lowest) placement number.
        """
        if not text:
            return None
        # Extract first number
        match = re.search(r"(\d+)", text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        return None

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    async def _load_event_ids(self) -> list[int]:
        ids = await self.db.get_all_ids("events", "event_id")
        if ids:
            return sorted(int(x) for x in ids)

        path = os.path.join(CHECKPOINT_DIR, "all_event_ids.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [int(x) for x in json.load(f)]
