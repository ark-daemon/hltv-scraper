"""
scrapers/players.py — Collect all player IDs and scrape player profile pages.
"""

import json
import os
import re
from datetime import datetime, timezone

from loguru import logger

from bs4 import BeautifulSoup

from config import BASE_URL, CHECKPOINT_DIR
from scrapers.base import BaseScraper


class PlayersScraper(BaseScraper):
    """
    Step 1: Paginate /stats/players to collect all player IDs.
    Step 2: Scrape each player profile page.
    """

    SCRAPER_KEY = "players"

    async def run(self) -> dict:
        logger.info(f"[{self.name}] Starting...")
        stats = {"processed": 0, "inserted": 0, "skipped": 0, "errors": 0}

        # Step 1 — Collect player IDs
        player_id_map = await self._collect_player_ids()

        # Also gather IDs from player_match_stats table
        db_player_ids = await self.db.get_all_ids("player_match_stats", "player_id")
        for pid in db_player_ids:
            if pid and pid not in player_id_map:
                player_id_map[pid] = f"player-{pid}"  # slug fallback

        self._save_player_ids(player_id_map)
        logger.info(f"[{self.name}] Total unique player IDs: {len(player_id_map)}")

        # Step 2 — Scrape profiles
        existing_ids = await self.db.get_all_ids("players", "player_id")
        done_set = self.checkpoint.get_done_set(self.SCRAPER_KEY)
        total = len(player_id_map)

        for i, (pid, slug) in enumerate(player_id_map.items()):
            if pid in existing_ids or str(pid) in done_set:
                stats["skipped"] += 1
                continue

            url = f"{BASE_URL}/player/{pid}/{slug}"
            soup = await self.fetch(url)
            if soup is None:
                stats["errors"] += 1
                continue

            player_data = self._parse_player_profile(soup, pid, url)
            if player_data:
                inserted = await self.db.insert_or_ignore("players", player_data)
                if inserted:
                    stats["inserted"] += 1

            self.checkpoint.mark_done(self.SCRAPER_KEY, pid)
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

    async def _collect_player_ids(self) -> dict[int, str]:
        """Paginate /stats/players to get all player IDs and slugs."""
        player_id_map: dict[int, str] = {}
        offset = 0

        while True:
            url = f"{BASE_URL}/stats/players?startDate=all&offset={offset}"
            soup = await self.fetch(url)
            if soup is None:
                break

            rows = soup.select(
                "table.stats-table tbody tr, div.players-container .player-row"
            )
            if not rows:
                # Try alternate selectors
                rows = soup.select("tr.player-overview-row")
            if not rows:
                logger.info(f"[{self.name}] No more player rows at offset={offset}.")
                break

            new_found = 0
            for row in rows:
                link = row.select_one("a[href*='/player/']")
                if not link:
                    continue
                href = link.get("href", "")
                pid = self.extract_id_from_url(href, -2)
                slug_parts = href.rstrip("/").split("/")
                slug = slug_parts[-1] if slug_parts else "player"
                if pid and pid not in player_id_map:
                    player_id_map[pid] = slug
                    new_found += 1

            if new_found == 0:
                break

            logger.debug(
                f"[{self.name}] Collected {len(player_id_map)} players so far (offset={offset})"
            )
            offset += 50

        return player_id_map

    # ------------------------------------------------------------------
    # Profile parsing
    # ------------------------------------------------------------------

    def _parse_player_profile(self, soup: BeautifulSoup, player_id: int, url: str) -> dict | None:
        """Extract all fields from a player profile page."""
        try:
            # Nickname
            nick_el = soup.select_one(
                "h1.playerNickname, span.player-nick, h1.summaryNickname"
            )
            nickname = self.safe_text(nick_el)

            # Real name
            name_el = soup.select_one("div.playerRealname, span.summaryRealname")
            real_name = self.safe_text(name_el)

            # Country
            country_el = soup.select_one("img.flag, span.flag")
            country = None
            country_code = None
            if country_el:
                country = country_el.get("alt") or country_el.get("title")
                src = country_el.get("src", "")
                # Extract code from URL like /img/static/flags/30x20/DK.gif
                cc_match = re.search(r"/([A-Z]{2})\.(gif|png|svg)", src, re.IGNORECASE)
                if cc_match:
                    country_code = cc_match.group(1).upper()

            # Age
            age_el = soup.select_one("span.playerAge, div.summaryPlayerAge")
            age_text = self.safe_text(age_el)
            age = None
            birth_date = None
            if age_text:
                age_match = re.search(r"(\d+)\s*years?", age_text, re.IGNORECASE)
                if age_match:
                    age = int(age_match.group(1))
                date_match = re.search(
                    r"(\w+\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2})", age_text
                )
                if date_match:
                    birth_date = date_match.group(1)

            # Team
            team_el = soup.select_one("div.playerTeam a, a[href*='/team/']")
            team_name = self.safe_text(team_el)
            team_id = None
            if team_el:
                team_id = self.extract_id_from_url(team_el.get("href", ""), -2)

            # Role
            role_el = soup.select_one("span.playerPosition, div.summaryPlayerPos")
            role = self.safe_text(role_el)

            # Twitter / Twitch
            twitter = None
            twitch = None
            for a in soup.select("a[href*='twitter.com'], a[href*='x.com']"):
                twitter = a.get("href")
                break
            for a in soup.select("a[href*='twitch.tv']"):
                twitch = a.get("href")
                break

            # Photo
            photo_el = soup.select_one(
                "img.bodyshot-img, img.playerBodyshot, img.summaryBodyshot"
            )
            photo_url = self.safe_attr(photo_el, "src")

            # Retired?
            is_retired = 0
            if soup.select_one("div.retired-notice, span.retired") or (
                soup.find(string=lambda t: t and "retired" in t.lower())
            ):
                is_retired = 1

            return {
                "player_id": player_id,
                "nickname": nickname,
                "real_name": real_name,
                "country": country,
                "country_code": country_code,
                "age": age,
                "birth_date": birth_date,
                "team_id": team_id,
                "team_name": team_name,
                "role": role,
                "twitter": twitter,
                "twitch": twitch,
                "hltv_url": url,
                "photo_url": photo_url,
                "is_retired": is_retired,
                "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        except Exception as e:
            logger.error(f"[{self.name}] Error parsing player {player_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def _save_player_ids(self, player_id_map: dict[int, str]) -> None:
        path = os.path.join(CHECKPOINT_DIR, "all_player_ids.json")
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump({str(k): v for k, v in player_id_map.items()}, f)
        logger.info(f"[{self.name}] Saved {len(player_id_map)} player IDs to {path}")
