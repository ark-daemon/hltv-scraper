"""
scrapers/teams.py - Collect all team IDs and scrape team profile pages.
"""

import json
import os
import re
from datetime import datetime, timezone

from loguru import logger

from bs4 import BeautifulSoup

from config import BASE_URL, CHECKPOINT_DIR
from scrapers.base import BaseScraper


class TeamsScraper(BaseScraper):
    """
    Step 1: Paginate /stats/teams to collect all team IDs.
    Step 2: Scrape each team profile page.
    """

    SCRAPER_KEY = "teams"

    async def run(self) -> dict:
        logger.info(f"[{self.name}] Starting...")
        stats = {"processed": 0, "inserted": 0, "skipped": 0, "errors": 0}

        # Step 1 - Collect team IDs
        team_id_map = await self._collect_team_ids()

        # Also pull team IDs from matches table
        for col in ("team1_id", "team2_id"):
            db_ids = await self.db.get_all_ids("matches", col)
            for tid in db_ids:
                if tid and tid not in team_id_map:
                    team_id_map[tid] = f"team-{tid}"

        self._save_team_ids(team_id_map)
        logger.info(f"[{self.name}] Total unique team IDs: {len(team_id_map)}")

        # Step 2 - Scrape profiles
        existing_ids = await self.db.get_all_ids("teams", "team_id")
        done_set = self.checkpoint.get_done_set(self.SCRAPER_KEY)
        total = len(team_id_map)

        for i, (tid, slug) in enumerate(team_id_map.items()):
            if tid in existing_ids or str(tid) in done_set:
                stats["skipped"] += 1
                continue

            url = f"{BASE_URL}/team/{tid}/{slug}"
            soup = await self.fetch(url)
            if soup is None:
                stats["errors"] += 1
                continue

            team_data = self._parse_team_profile(soup, tid, url)
            if team_data:
                inserted = await self.db.insert_or_ignore("teams", team_data)
                if inserted:
                    stats["inserted"] += 1

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
    # Collection
    # ------------------------------------------------------------------

    async def _collect_team_ids(self) -> dict[int, str]:
        """Paginate /stats/teams to get all team IDs and slugs."""
        team_id_map: dict[int, str] = {}
        offset = 0

        while True:
            url = f"{BASE_URL}/stats/teams?offset={offset}"
            soup = await self.fetch(url)
            if soup is None:
                break

            rows = soup.select("table.stats-table tbody tr")
            if not rows:
                break

            new_found = 0
            for row in rows:
                link = row.select_one("a[href*='/team/']")
                if not link:
                    continue
                href = link.get("href", "")
                tid = self.extract_id_from_url(href, -2)
                slug_parts = href.rstrip("/").split("/")
                slug = slug_parts[-1] if slug_parts else "team"
                if tid and tid not in team_id_map:
                    team_id_map[tid] = slug
                    new_found += 1

            if new_found == 0:
                break

            logger.debug(
                f"[{self.name}] Collected {len(team_id_map)} teams so far (offset={offset})"
            )
            offset += 50

        return team_id_map

    # ------------------------------------------------------------------
    # Profile parsing
    # ------------------------------------------------------------------

    def _parse_team_profile(self, soup: BeautifulSoup, team_id: int, url: str) -> dict | None:
        """Extract all fields from a team profile page."""
        try:
            # Name
            name_el = soup.select_one(
                "h1.profile-team-name, div.team-header-teamname, h1.teamName"
            )
            name = self.safe_text(name_el)

            # Country
            country_el = soup.select_one("img.flag, div.team-country img")
            country = None
            country_code = None
            if country_el:
                country = country_el.get("alt") or country_el.get("title")
                src = country_el.get("src", "")
                cc_match = re.search(r"/([A-Z]{2})\.(gif|png|svg)", src, re.IGNORECASE)
                if cc_match:
                    country_code = cc_match.group(1).upper()

            # Logo
            logo_el = soup.select_one("img.teamlogo, div.teamlogo img")
            logo_url = self.safe_attr(logo_el, "src")

            # World ranking
            ranking_el = soup.select_one(
                "div.ranking-info span.value, div.team-ranking span"
            )
            world_ranking = self.parse_int(self.safe_text(ranking_el))

            # HLTV points
            points_el = soup.select_one("div.points span.value")
            hltv_points = self.parse_int(self.safe_text(points_el))

            # Weeks in top 30
            weeks_el = soup.select_one("div.weeks-in-top30 span.value")
            weeks_in_top30 = self.parse_int(self.safe_text(weeks_el))

            # Avg player age
            age_el = soup.select_one("div.average-age span.value")
            avg_player_age = self.parse_float(self.safe_text(age_el))

            # Coach
            coach_el = soup.select_one(
                "a[href*='/player/'][title*='coach'], span.coach-holder a"
            )
            coach = self.safe_text(coach_el)
            coach_id = None
            if coach_el:
                coach_id = self.extract_id_from_url(coach_el.get("href", ""), -2)

            # Active / disbanded?
            is_active = 1
            if soup.select_one("div.disbanded-notice") or soup.find(
                string=lambda t: t and "disbanded" in t.lower()
            ):
                is_active = 0

            return {
                "team_id": team_id,
                "name": name,
                "country": country,
                "country_code": country_code,
                "logo_url": logo_url,
                "hltv_url": url,
                "world_ranking": world_ranking,
                "hltv_points": hltv_points,
                "weeks_in_top30": weeks_in_top30,
                "avg_player_age": avg_player_age,
                "coach": coach,
                "coach_id": coach_id,
                "is_active": is_active,
                "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        except Exception as e:
            logger.error(f"[{self.name}] Error parsing team {team_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def _save_team_ids(self, team_id_map: dict[int, str]) -> None:
        path = os.path.join(CHECKPOINT_DIR, "all_team_ids.json")
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump({str(k): v for k, v in team_id_map.items()}, f)
        logger.info(f"[{self.name}] Saved {len(team_id_map)} team IDs to {path}")
