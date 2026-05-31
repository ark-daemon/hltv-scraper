"""
scrapers/team_stats.py — Scrape overall and per-map stats for each team.
"""

import json
import os
import re
from datetime import datetime, timezone

from loguru import logger

from config import BASE_URL, CHECKPOINT_DIR
from scrapers.base import BaseScraper


class TeamStatsScraper(BaseScraper):
    """
    For each team:
      1. Overall stats  → team_stats
      2. Per-map stats  → team_map_stats
    """

    SCRAPER_KEY = "team_stats"

    async def run(self) -> dict:
        logger.info(f"[{self.name}] Starting...")
        stats = {"processed": 0, "inserted": 0, "skipped": 0, "errors": 0}

        team_id_map = self._load_team_ids()
        if not team_id_map:
            logger.warning(f"[{self.name}] No team IDs found. Run TeamsScraper first.")
            return stats

        done_set = self.checkpoint.get_done_set(self.SCRAPER_KEY)
        total = len(team_id_map)
        logger.info(f"[{self.name}] Processing stats for {total} teams.")

        for i, (tid_str, slug) in enumerate(team_id_map.items()):
            tid = int(tid_str)
            if str(tid) in done_set:
                stats["skipped"] += 1
                continue

            inserted_count = 0
            errors = 0
            write_failed = False

            # 1. Overall stats
            overall_url = f"{BASE_URL}/stats/teams/{tid}/{slug}"
            soup = await self.fetch(overall_url)
            if soup:
                overall_row = self._parse_overall_stats(soup, tid)
                if overall_row:
                    ok = await self.db.insert_or_replace("team_stats", overall_row)
                    if ok:
                        inserted_count += 1
                    else:
                        write_failed = True
                        errors += 1
            else:
                errors += 1

            # 2. Map stats
            maps_url = f"{BASE_URL}/stats/teams/maps/{tid}/{slug}"
            soup = await self.fetch(maps_url)
            if soup:
                map_rows = self._parse_map_stats(soup, tid)
                if map_rows:
                    n = await self.db.bulk_insert(
                        "team_map_stats", map_rows, replace=True
                    )
                    inserted_count += n
                    if n <= 0:
                        write_failed = True
                        errors += 1
            else:
                errors += 1

            stats["inserted"] += inserted_count
            stats["errors"] += errors
            if inserted_count > 0 and not write_failed:
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
    # Overall stats
    # ------------------------------------------------------------------

    def _parse_overall_stats(self, soup, team_id: int) -> dict | None:
        """Extract team overall stats from the stats page."""
        try:
            stat_map = {}
            for row in soup.select("div.stats-row"):
                spans = row.select("span")
                if len(spans) >= 2:
                    label = (self.safe_text(spans[0]) or "").lower()
                    value = self.safe_text(spans[1])
                    stat_map[label] = value

            # Also scan table rows
            for tr in soup.select("tr"):
                tds = tr.select("td")
                if len(tds) >= 2:
                    label = (self.safe_text(tds[0]) or "").lower()
                    value = self.safe_text(tds[1])
                    stat_map[label] = value

            def get(*keys):
                for k in keys:
                    if k in stat_map:
                        return stat_map[k]
                return None

            maps_played = self.parse_int(get("maps played", "maps"))
            wins = self.parse_int(get("wins"))
            losses = self.parse_int(get("losses"))
            draws = self.parse_int(get("draws"))
            win_rate = None
            if wins is not None and maps_played:
                win_rate = round(wins / maps_played * 100, 1)

            return {
                "team_id": team_id,
                "maps_played": maps_played,
                "wins": wins,
                "losses": losses,
                "draws": draws,
                "win_rate": self.parse_float(get("win rate")) or win_rate,
                "rounds_played": self.parse_int(get("rounds played", "rounds")),
                "rounds_won": self.parse_int(get("rounds won")),
                "rounds_lost": self.parse_int(get("rounds lost")),
                "kd_ratio": self.parse_float(get("k/d ratio", "k/d")),
                "rating": self.parse_float(get("rating 2.0", "rating")),
                "period_from": "all",
                "period_to": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            }
        except Exception as e:
            logger.error(
                f"[{self.name}] Error parsing overall stats for team {team_id}: {e}"
            )
            return None

    # ------------------------------------------------------------------
    # Map stats
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

    def _parse_map_stats(self, soup, team_id: int) -> list[dict]:
        """Extract per-map stats for a team."""
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

                maps_played = self.parse_int(
                    self._get_cell_text(tds, col_map, ["maps"], fallback_idx=1)
                )
                wins = self.parse_int(
                    self._get_cell_text(tds, col_map, ["wins"], fallback_idx=2)
                )
                losses = self.parse_int(
                    self._get_cell_text(tds, col_map, ["losses"], fallback_idx=3)
                )
                win_rate = None
                if wins is not None and maps_played:
                    win_rate = round(wins / maps_played * 100, 1)

                rows.append(
                    {
                        "team_id": team_id,
                        "map_name": self._get_cell_text(
                            tds, col_map, ["map"], fallback_idx=0
                        ),
                        "maps_played": maps_played,
                        "wins": wins,
                        "losses": losses,
                        "win_rate": self.parse_float(
                            self._get_cell_text(
                                tds, col_map, ["win rate", "win%"], fallback_idx=4
                            )
                        )
                        or win_rate,
                        "ct_rounds_won": self.parse_int(
                            self._get_cell_text(
                                tds, col_map, ["ct rounds won", "ct"], fallback_idx=5
                            )
                        ),
                        "t_rounds_won": self.parse_int(
                            self._get_cell_text(
                                tds, col_map, ["t rounds won", "t"], fallback_idx=6
                            )
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

    def _load_team_ids(self) -> dict[str, str]:
        path = os.path.join(CHECKPOINT_DIR, "all_team_ids.json")
        if not os.path.exists(path):
            return {}
        with open(path) as f:
            return json.load(f)
