"""
scrapers/player_stats.py — Scrape career, per-event, and per-map stats for each player.
"""

import json
import os
import re
from datetime import datetime, timezone

from loguru import logger

from bs4 import BeautifulSoup

from config import BASE_URL, CHECKPOINT_DIR
from scrapers.base import BaseScraper


class PlayerStatsScraper(BaseScraper):
    """
    For each player:
      1. Career stats  → player_career_stats
      2. Per-event     → player_event_stats
      3. Per-map-type  → player_map_stats
    """

    SCRAPER_KEY = "player_stats"

    async def run(self) -> dict:
        logger.info(f"[{self.name}] Starting...")
        stats = {"processed": 0, "inserted": 0, "skipped": 0, "errors": 0}

        player_id_map = self._load_player_ids()
        if not player_id_map:
            # Fallback: query DB for known player IDs
            db_ids = await self.db.get_all_ids("players", "player_id")
            if not db_ids:
                db_ids = await self.db.get_all_ids("player_match_stats", "player_id")
            player_id_map = {str(pid): f"player-{pid}" for pid in db_ids if pid}
            if player_id_map:
                logger.info(
                    f"[{self.name}] Fallback: loaded {len(player_id_map)} player IDs from DB."
                )
        if not player_id_map:
            logger.warning(
                f"[{self.name}] No player IDs found. Run PlayersScraper first."
            )
            return stats

        done_set = self.checkpoint.get_done_set(self.SCRAPER_KEY)
        total = len(player_id_map)
        logger.info(f"[{self.name}] Processing stats for {total} players.")

        for i, (pid_str, slug) in enumerate(player_id_map.items()):
            pid = int(pid_str)
            if str(pid) in done_set:
                stats["skipped"] += 1
                continue

            inserted_count = 0
            errors = 0
            write_failed = False

            # 1. Career stats
            career_url = f"{BASE_URL}/stats/players/{pid}/{slug}"
            soup = await self.fetch(career_url)
            if soup:
                career_row = self._parse_career_stats(soup, pid)
                if career_row:
                    ok = await self.db.insert_or_replace(
                        "player_career_stats", career_row
                    )
                    if ok:
                        inserted_count += 1
                    else:
                        write_failed = True
                        errors += 1
            else:
                errors += 1

            # 2. Per-event stats
            events_url = f"{BASE_URL}/stats/players/events/{pid}/{slug}"
            soup = await self.fetch(events_url)
            if soup:
                event_rows = self._parse_event_stats(soup, pid)
                if event_rows:
                    n = await self.db.bulk_insert(
                        "player_event_stats", event_rows, replace=True
                    )
                    inserted_count += n
                    if n < 0:
                        write_failed = True
                        errors += 1
            else:
                errors += 1

            # 3. Per-map stats
            maps_url = f"{BASE_URL}/stats/players/maps/{pid}/{slug}"
            soup = await self.fetch(maps_url)
            if soup:
                map_rows = self._parse_map_stats(soup, pid)
                if map_rows:
                    n = await self.db.bulk_insert(
                        "player_map_stats", map_rows, replace=True
                    )
                    inserted_count += n
                    if n < 0:
                        write_failed = True
                        errors += 1
            else:
                errors += 1

            stats["inserted"] += inserted_count
            stats["errors"] += errors
            if inserted_count > 0 and not write_failed:
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
    # Career stats
    # ------------------------------------------------------------------

    def _parse_career_stats(self, soup: BeautifulSoup, player_id: int) -> dict | None:
        """Extract career stat values from the stats overview page."""
        try:
            stat_map = {}
            # Stats are in div.stats-row pairs
            for row in soup.select("div.stats-row"):
                spans = row.select("span")
                if len(spans) >= 2:
                    label = self.safe_text(spans[0]) or ""
                    value = self.safe_text(spans[1])
                    stat_map[label.lower()] = value

            # Also try table cells
            for row in soup.select("tr"):
                tds = row.select("td")
                if len(tds) >= 2:
                    label = (self.safe_text(tds[0]) or "").lower()
                    value = self.safe_text(tds[1])
                    stat_map[label] = value

            def get(*keys):
                for k in keys:
                    if k in stat_map:
                        return stat_map[k]
                return None

            return {
                "player_id": player_id,
                "maps_played": self.parse_int(get("maps played", "maps")),
                "rounds_played": self.parse_int(get("rounds played", "rounds")),
                "kills": self.parse_int(get("kills")),
                "deaths": self.parse_int(get("deaths")),
                "assists": self.parse_int(get("assists")),
                "rating_2": self.parse_float(get("rating 2.0", "rating")),
                "kd_ratio": self.parse_float(get("k/d ratio", "k/d")),
                "kd_diff": self.parse_int(get("k-d diff.", "k-d diff")),
                "hs_percent": self.parse_percent(get("headshot %", "hs%")),
                "headshots": self.parse_int(get("headshots")),
                "kast": self.parse_percent(get("kast")),
                "adr": self.parse_float(get("adr", "average damage per round")),
                "impact": self.parse_float(get("impact")),
                "dpr": self.parse_float(get("dpr", "deaths per round")),
                "spr": self.parse_float(get("spr", "saved by teammate per round")),
                "opening_kills": self.parse_int(get("opening kills", "first kills")),
                "opening_deaths": self.parse_int(get("opening deaths", "first deaths")),
                "opening_ratio": self.parse_float(get("opening kill ratio")),
                "opening_rating": self.parse_float(get("opening kill rating")),
                "rifle_kills": self.parse_int(get("rifle kills")),
                "sniper_kills": self.parse_int(get("sniper kills")),
                "smg_kills": self.parse_int(get("smg kills")),
                "pistol_kills": self.parse_int(get("pistol kills")),
                "grenade_kills": self.parse_int(get("grenade kills")),
                "mvp_stars": self.parse_int(get("mvp", "mvps")),
                "period_from": "all",
                "period_to": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            }
        except Exception as e:
            logger.error(
                f"[{self.name}] Error parsing career stats for {player_id}: {e}"
            )
            return None

    # ------------------------------------------------------------------
    # Event stats
    # ------------------------------------------------------------------

    def _build_column_map(self, table) -> dict[str, int]:
        """Build normalized header-text -> column index mapping."""
        col_map: dict[str, int] = {}
        header_cells = table.select("thead tr th")
        for idx, th in enumerate(header_cells):
            raw = " ".join(th.stripped_strings)
            normalized = re.sub(r"\s+", " ", raw).strip().lower()
            if normalized:
                col_map[normalized] = idx
        return col_map

    def _get_cell_text(
        self,
        tds,
        col_map: dict[str, int],
        header_keys: list[str],
        fallback_idx: int | None = None,
    ) -> str | None:
        for key in header_keys:
            for header, idx in col_map.items():
                if key in header and idx < len(tds):
                    return self.safe_text(tds[idx])
        if fallback_idx is not None and fallback_idx < len(tds):
            return self.safe_text(tds[fallback_idx])
        return None

    def _parse_event_stats(self, soup: BeautifulSoup, player_id: int) -> list[dict]:
        """Extract per-event stats rows."""
        rows = []
        table = soup.select_one("table.stats-table")
        if table is None:
            return rows

        col_map = self._build_column_map(table)
        for tr in table.select("tbody tr"):
            try:
                tds = tr.select("td")
                if len(tds) < 5:
                    continue

                event_link = tr.select_one("a[href*='/events/']")
                event_id = None
                event_name = None
                if event_link:
                    event_id = self.extract_id_from_url(event_link.get("href", ""), -2)
                    event_name = self.safe_text(event_link)

                rows.append(
                    {
                        "player_id": player_id,
                        "event_id": event_id,
                        "event_name": event_name,
                        "maps_played": self.parse_int(
                            self._get_cell_text(tds, col_map, ["maps"], fallback_idx=1)
                        ),
                        "rounds_played": self.parse_int(
                            self._get_cell_text(tds, col_map, ["rounds"], fallback_idx=2)
                        ),
                        "rating_2": self.parse_float(
                            self._get_cell_text(tds, col_map, ["rating"], fallback_idx=3)
                        ),
                        "kd_ratio": self.parse_float(
                            self._get_cell_text(tds, col_map, ["k/d"], fallback_idx=4)
                        ),
                        "kd_diff": self.parse_int(
                            self._get_cell_text(
                                tds, col_map, ["+/-", "k-d", "diff"], fallback_idx=5
                            )
                        ),
                        "hs_percent": self.parse_percent(
                            self._get_cell_text(tds, col_map, ["hs", "headshot"], fallback_idx=6)
                        ),
                        "kast": self.parse_percent(
                            self._get_cell_text(tds, col_map, ["kast"], fallback_idx=7)
                        ),
                        "adr": self.parse_float(
                            self._get_cell_text(tds, col_map, ["adr"], fallback_idx=8)
                        ),
                        "kills": self.parse_int(
                            self._get_cell_text(tds, col_map, [" kills", "k"], fallback_idx=9)
                        ),
                        "deaths": self.parse_int(
                            self._get_cell_text(tds, col_map, [" deaths", "d"], fallback_idx=10)
                        ),
                        "assists": self.parse_int(
                            self._get_cell_text(tds, col_map, [" assists", "a"], fallback_idx=11)
                        ),
                    }
                )
            except Exception as e:
                logger.debug(f"[{self.name}] Error parsing event stats row: {e}")
                continue
        return rows

    # ------------------------------------------------------------------
    # Map stats
    # ------------------------------------------------------------------

    def _parse_map_stats(self, soup: BeautifulSoup, player_id: int) -> list[dict]:
        """Extract per-map-type stats rows."""
        rows = []
        table = soup.select_one("table.stats-table")
        if table is None:
            return rows

        col_map = self._build_column_map(table)
        for tr in table.select("tbody tr"):
            try:
                tds = tr.select("td")
                if len(tds) < 4:
                    continue

                rows.append(
                    {
                        "player_id": player_id,
                        "map_name": self._get_cell_text(
                            tds, col_map, ["map"], fallback_idx=0
                        ),
                        "maps_played": self.parse_int(
                            self._get_cell_text(tds, col_map, ["maps"], fallback_idx=1)
                        ),
                        "rating_2": self.parse_float(
                            self._get_cell_text(tds, col_map, ["rating"], fallback_idx=2)
                        ),
                        "kd_ratio": self.parse_float(
                            self._get_cell_text(tds, col_map, ["k/d"], fallback_idx=3)
                        ),
                        "hs_percent": self.parse_percent(
                            self._get_cell_text(tds, col_map, ["hs", "headshot"], fallback_idx=4)
                        ),
                        "kast": self.parse_percent(
                            self._get_cell_text(tds, col_map, ["kast"], fallback_idx=5)
                        ),
                        "adr": self.parse_float(
                            self._get_cell_text(tds, col_map, ["adr"], fallback_idx=6)
                        ),
                        "kills": self.parse_int(
                            self._get_cell_text(tds, col_map, [" kills", "k"], fallback_idx=7)
                        ),
                        "deaths": self.parse_int(
                            self._get_cell_text(tds, col_map, [" deaths", "d"], fallback_idx=8)
                        ),
                    }
                )
            except Exception as e:
                logger.debug(f"[{self.name}] Error parsing map stats row: {e}")
                continue
        return rows

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def _load_player_ids(self) -> dict[str, str]:
        path = os.path.join(CHECKPOINT_DIR, "all_player_ids.json")
        if not os.path.exists(path):
            return {}
        with open(path) as f:
            return json.load(f)
