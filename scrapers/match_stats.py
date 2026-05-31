"""
scrapers/match_stats.py - Scrape full player scoreboard stats for each map played.

Reads mapstats entries from map_results (legacy JSON fallback supported).
Inserts player stats into player_match_stats table.
"""

import json
import os
import re

from loguru import logger

from config import BASE_URL, CHECKPOINT_DIR
from scrapers.base import BaseScraper


class MatchStatsScraper(BaseScraper):
    """
    For each mapstats entry, fetches the stat page and extracts
    full player scoreboards (overall, CT-side, T-side).
    """

    SCRAPER_KEY = "match_stats"

    async def run(self) -> dict:
        logger.info(f"[{self.name}] Starting...")
        stats = {"processed": 0, "inserted": 0, "skipped": 0, "errors": 0}

        entries = await self._load_mapstats_entries()
        if not entries:
            logger.warning(
                f"[{self.name}] No mapstats entries found in DB or legacy checkpoint."
            )
            return stats

        done_set = self.checkpoint.get_done_set(self.SCRAPER_KEY)
        total = len(entries)
        logger.info(f"[{self.name}] {total} mapstats entries to process.")

        for i, entry in enumerate(entries):
            mapstats_id = entry.get("mapstats_id")
            if not mapstats_id:
                continue

            if str(mapstats_id) in done_set:
                stats["skipped"] += 1
                continue

            url = (
                entry.get("mapstats_url")
                or f"{BASE_URL}/stats/matches/mapstatsid/{mapstats_id}/match"
            )
            soup = await self.fetch(url)
            if soup is None:
                stats["errors"] += 1
                continue

            match_id = entry.get("match_id")
            map_number = entry.get("map_number", 1)
            map_name = entry.get("map_name", "unknown")

            # Parse overall stats
            all_rows = self._parse_scoreboard(
                soup, match_id, map_number, map_name, side="overall"
            )

            # Check for CT / T side tabs
            ct_url = url + "?side=ct" if "?" not in url else url + "&side=ct"
            t_url = url + "?side=t" if "?" not in url else url + "&side=t"

            # Try CT tab
            ct_soup = await self.fetch(ct_url)
            if ct_soup:
                ct_rows = self._parse_scoreboard(
                    ct_soup, match_id, map_number, map_name, side="ct"
                )
                all_rows.extend(ct_rows)

            # Try T tab
            t_soup = await self.fetch(t_url)
            if t_soup:
                t_rows = self._parse_scoreboard(
                    t_soup, match_id, map_number, map_name, side="t"
                )
                all_rows.extend(t_rows)

            write_failed = False
            if all_rows:
                inserted = await self.db.bulk_insert(
                    "player_match_stats", all_rows, replace=True
                )
                stats["inserted"] += inserted
                if inserted <= 0:
                    write_failed = True
                    stats["errors"] += 1
                    logger.warning(
                        f"[{self.name}] DB write failed for mapstats_id={mapstats_id}; leaving pending."
                    )

            if all_rows and not write_failed:
                self.checkpoint.mark_done(self.SCRAPER_KEY, mapstats_id)
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

    def _parse_scoreboard(
        self, soup, match_id: int, map_number: int, map_name: str, side: str
    ) -> list[dict]:
        """Parse a player stats scoreboard table from a mapstats page."""
        rows = []

        # Find all stat tables (one per team)
        tables = soup.select("table.stats-table")
        for table in tables:
            col_map = self._build_column_map(table)

            # Team name from table heading
            team_header = table.find_previous(
                "div.team-left, div.team-right, div.teamName"
            )
            team_name = self.safe_text(team_header)
            team_id = None
            if team_header:
                a_tag = team_header.find("a") if hasattr(team_header, "find") else None
                if a_tag:
                    team_id = self.extract_id_from_url(a_tag.get("href", ""), -2)

            for tr in table.select("tbody tr"):
                row = self._parse_player_stat_row(
                    tr,
                    col_map,
                    match_id,
                    map_number,
                    map_name,
                    team_id,
                    team_name,
                    side,
                )
                if row:
                    rows.append(row)

        return rows

    def _build_column_map(self, table) -> dict[str, int]:
        """Build normalized header-text -> column index mapping for flexible parsing."""
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

    def _parse_hs_cell(self, text: str | None) -> tuple[float | None, int | None]:
        """Parse HS cell variants like '53.8%' or '7 (53.8%)' or '53.8% (7)'."""
        if not text:
            return None, None

        hs_percent = self.parse_percent(text)
        hs_kills = None

        # Common forms include bracketed number for raw HS kills.
        paren_nums = re.findall(r"\((\d+)\)", text)
        if paren_nums:
            hs_kills = self.parse_int(paren_nums[0])
        else:
            # If a plain leading integer exists alongside percent, treat as HS kills.
            leading = re.match(r"^(\d+)\s*", text.strip())
            if leading and "%" in text:
                hs_kills = self.parse_int(leading.group(1))

        return hs_percent, hs_kills

    def _parse_player_stat_row(
        self,
        tr,
        col_map: dict[str, int],
        match_id,
        map_number,
        map_name,
        team_id,
        team_name,
        side,
    ) -> dict | None:
        """Extract a single player's stats from a scoreboard table row."""
        try:
            tds = tr.select("td")
            if len(tds) < 5:
                return None

            # Player name/id
            player_link = tr.select_one("a[href*='/stats/players/']")
            if not player_link:
                return None
            player_href = player_link.get("href", "")
            player_id = self.extract_id_from_url(player_href, -2)
            player_name = self.safe_text(player_link)

            kills = self.parse_int(
                self._get_cell_text(tds, col_map, [" kills", "k"], fallback_idx=1)
            )
            deaths = self.parse_int(
                self._get_cell_text(tds, col_map, [" deaths", "d"], fallback_idx=2)
            )
            assists = self.parse_int(
                self._get_cell_text(tds, col_map, [" assists", "a"], fallback_idx=3)
            )

            kd_diff = self.parse_int(
                self._get_cell_text(
                    tds, col_map, ["+/-", "k-d", "diff"], fallback_idx=4
                )
            )
            kast = self.parse_percent(
                self._get_cell_text(tds, col_map, ["kast"], fallback_idx=5)
            )
            adr = self.parse_float(
                self._get_cell_text(tds, col_map, ["adr"], fallback_idx=6)
            )

            rating_text = self._get_cell_text(
                tds, col_map, ["rating"], fallback_idx=len(tds) - 1
            )
            rating_2 = self.parse_float(rating_text)

            hs_text = self._get_cell_text(
                tds,
                col_map,
                ["hs", "headshot"],
                fallback_idx=8 if len(tds) > 8 else None,
            )
            hs_percent, hs_kills = self._parse_hs_cell(hs_text)

            flash_assists = self.parse_int(
                self._get_cell_text(tds, col_map, ["flash assist", "fa"])
            )
            opening_kills = self.parse_int(
                self._get_cell_text(tds, col_map, ["opening kill", "first kill", "fk"])
            )
            opening_deaths = self.parse_int(
                self._get_cell_text(
                    tds, col_map, ["opening death", "first death", "fd"]
                )
            )

            k1 = self.parse_int(self._get_cell_text(tds, col_map, ["1k"]))
            k2 = self.parse_int(self._get_cell_text(tds, col_map, ["2k"]))
            k3 = self.parse_int(self._get_cell_text(tds, col_map, ["3k"]))
            k4 = self.parse_int(self._get_cell_text(tds, col_map, ["4k"]))
            k5 = self.parse_int(self._get_cell_text(tds, col_map, ["5k"]))

            # If unavailable in this page layout, we keep null by design after explicit header scan.
            first_kills_ct, first_kills_t = None, None
            first_deaths_ct, first_deaths_t = None, None
            if side == "ct":
                first_kills_ct = opening_kills
                first_deaths_ct = opening_deaths
            elif side == "t":
                first_kills_t = opening_kills
                first_deaths_t = opening_deaths

            opening_ratio = None
            if (
                opening_kills is not None
                and opening_deaths is not None
                and opening_deaths > 0
            ):
                opening_ratio = round(opening_kills / opening_deaths, 2)

            kd_ratio = None
            if kills is not None and deaths is not None and deaths > 0:
                kd_ratio = round(kills / deaths, 2)

            return {
                "match_id": match_id,
                "map_name": map_name,
                "map_number": map_number,
                "player_id": player_id,
                "player_name": player_name,
                "team_id": team_id,
                "team_name": team_name,
                "kills": kills,
                "deaths": deaths,
                "assists": assists,
                "rating_2": rating_2,
                "adr": adr,
                "kast": kast,
                "hs_kills": hs_kills,
                "hs_percent": hs_percent,
                "flash_assists": flash_assists,
                "opening_kills": opening_kills,
                "opening_deaths": opening_deaths,
                "opening_ratio": opening_ratio,
                "kd_diff": kd_diff,
                "kd_ratio": kd_ratio,
                "first_kills_ct": first_kills_ct,
                "first_kills_t": first_kills_t,
                "first_deaths_ct": first_deaths_ct,
                "first_deaths_t": first_deaths_t,
                "k1": k1,
                "k2": k2,
                "k3": k3,
                "k4": k4,
                "k5": k5,
                "side": side,
            }
        except Exception as e:
            logger.debug(f"[{self.name}] Error parsing player stat row: {e}")
            return None

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    async def _load_mapstats_entries(self) -> list[dict]:
        rows = await self.db.execute(
            """
            SELECT DISTINCT
                mapstats_id,
                match_id,
                map_number,
                map_name,
                mapstats_url
            FROM map_results
            WHERE mapstats_id IS NOT NULL
            ORDER BY match_id, map_number
            """
        )
        if rows:
            return [
                {
                    "mapstats_id": int(r[0]),
                    "match_id": r[1],
                    "map_number": r[2] or 1,
                    "map_name": r[3] or "unknown",
                    "mapstats_url": r[4]
                    or f"{BASE_URL}/stats/matches/mapstatsid/{int(r[0])}/match",
                }
                for r in rows
            ]

        path = os.path.join(CHECKPOINT_DIR, "mapstats_ids.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            if not data:
                return []
            if isinstance(data[0], int):
                return [
                    {
                        "mapstats_id": int(mid),
                        "match_id": None,
                        "map_number": 1,
                        "map_name": "unknown",
                        "mapstats_url": f"{BASE_URL}/stats/matches/mapstatsid/{int(mid)}/match",
                    }
                    for mid in data
                ]
            return data
