"""
scrapers/roster_history.py — Scrape player roster history for each team.
"""

import json
import os

from dateutil import parser as dateparser
from loguru import logger

from bs4 import BeautifulSoup

from config import BASE_URL, CHECKPOINT_DIR
from scrapers.base import BaseScraper


class RosterHistoryScraper(BaseScraper):
    """
    For each team, scrapes the lineup change history and current roster.
    Inserts rows into roster_history table.
    """

    SCRAPER_KEY = "roster_history"

    async def run(self) -> dict:
        logger.info(f"[{self.name}] Starting...")
        stats = {"processed": 0, "inserted": 0, "skipped": 0, "errors": 0}

        team_id_map = self._load_team_ids()
        if not team_id_map:
            # Fallback: query DB for known team IDs
            db_ids = await self.db.get_all_ids("teams", "team_id")
            if not db_ids:
                t1_ids = await self.db.get_all_ids("matches", "team1_id")
                t2_ids = await self.db.get_all_ids("matches", "team2_id")
                db_ids = t1_ids | t2_ids
            team_id_map = {str(tid): f"team-{tid}" for tid in db_ids if tid}
            if team_id_map:
                logger.info(
                    f"[{self.name}] Fallback: loaded {len(team_id_map)} team IDs from DB."
                )
        if not team_id_map:
            logger.warning(f"[{self.name}] No team IDs found. Run TeamsScraper first.")
            return stats

        done_set = self.checkpoint.get_done_set(self.SCRAPER_KEY)
        total = len(team_id_map)
        logger.info(f"[{self.name}] Processing roster history for {total} teams.")

        for i, (tid_str, slug) in enumerate(team_id_map.items()):
            tid = int(tid_str)
            if str(tid) in done_set:
                stats["skipped"] += 1
                continue

            url = f"{BASE_URL}/team/{tid}/{slug}"
            soup = await self.fetch(url)
            if soup is None:
                stats["errors"] += 1
                continue

            rows = self._parse_roster_history(soup, tid)
            if rows:
                n = await self.db.bulk_insert("roster_history", rows, replace=True)
                stats["inserted"] += n

            self.checkpoint.mark_done(self.SCRAPER_KEY, tid)
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

    def _parse_roster_history(self, soup: BeautifulSoup, team_id: int) -> list[dict]:
        """Extract lineup history and current roster from a team page."""
        rows = []
        seen_player_ids = set()

        # ── Current roster ──────────────────────────────────────────────
        for player_el in soup.select("div.squad-member, div.team-player"):
            try:
                link = player_el.select_one("a[href*='/player/']")
                if not link:
                    continue
                href = link.get("href", "")
                player_id = self.extract_id_from_url(href, -2)
                player_name = self.safe_text(link)
                if not player_id:
                    continue

                is_coach = 1 if "coach" in (player_el.get("class") or []) else 0
                # Also check nearby text
                role_el = player_el.select_one("span.position, div.position")
                if role_el and "coach" in (self.safe_text(role_el) or "").lower():
                    is_coach = 1

                rows.append(
                    {
                        "team_id": team_id,
                        "player_id": player_id,
                        "player_name": player_name,
                        "date_joined": None,
                        "date_left": None,
                        "is_active": 1,
                        "is_coach": is_coach,
                    }
                )
                seen_player_ids.add(player_id)
            except Exception as e:
                logger.debug(f"[{self.name}] Error parsing current roster entry: {e}")

        # ── Lineup changes history ──────────────────────────────────────
        history_section = soup.select_one(
            "div.roster-history, div.lineups-container, div.team-roster-history"
        )
        if not history_section:
            # Try to find a section by heading text
            for heading in soup.select("div.standard-headline, h2"):
                text = (self.safe_text(heading) or "").lower()
                if "lineup" in text or "roster" in text:
                    history_section = heading.find_next_sibling()
                    break

        if history_section:
            for entry in history_section.select(
                "div.lineup-change, tr.roster-change, div.change-row"
            ):
                try:
                    link = entry.select_one("a[href*='/player/']")
                    if not link:
                        continue
                    href = link.get("href", "")
                    player_id = self.extract_id_from_url(href, -2)
                    player_name = self.safe_text(link)

                    # Date joined / left
                    date_joined = None
                    date_left = None

                    date_els = entry.select("span.date, td.date, div.date")
                    if len(date_els) >= 1:
                        date_joined = self._parse_date(self.safe_text(date_els[0]))
                    if len(date_els) >= 2:
                        left_text = self.safe_text(date_els[1])
                        if left_text and "present" not in left_text.lower():
                            date_left = self._parse_date(left_text)

                    is_active = 1 if date_left is None else 0
                    is_coach = 0
                    if entry.find(string=lambda t: t and "coach" in t.lower()):
                        is_coach = 1

                    # Skip if already in current roster (avoid duplicate)
                    if player_id in seen_player_ids and is_active:
                        continue

                    rows.append(
                        {
                            "team_id": team_id,
                            "player_id": player_id,
                            "player_name": player_name,
                            "date_joined": date_joined,
                            "date_left": date_left,
                            "is_active": is_active,
                            "is_coach": is_coach,
                        }
                    )
                except Exception as e:
                    logger.debug(
                        f"[{self.name}] Error parsing roster history entry: {e}"
                    )

        return rows

    def _parse_date(self, text: str | None) -> str | None:
        """Parse a date string to ISO format, returning None on failure."""
        if not text:
            return None
        text = text.strip()
        if not text or text.lower() in ("present", "current", "-", ""):
            return None
        try:
            return dateparser.parse(text, fuzzy=True).strftime("%Y-%m-%d")
        except Exception:
            # Leave unset when parsing fails; never return partial year strings.
            return None

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def _load_team_ids(self) -> dict[str, str]:
        path = os.path.join(CHECKPOINT_DIR, "all_team_ids.json")
        if not os.path.exists(path):
            return {}
        with open(path) as f:
            return json.load(f)
