"""
scrapers/match_detail.py - Scrape per-map results and mapstats IDs for each match.

Reads match IDs from the matches table (legacy JSON fallback supported).
Writes discovered mapstats URLs to checkpoints/mapstats_ids.json for compatibility.
"""

import json
import os
import re

from loguru import logger

from bs4 import BeautifulSoup

from config import BASE_URL, CHECKPOINT_DIR
from scrapers.base import BaseScraper


class MatchDetailScraper(BaseScraper):
    """
    For each match, fetches the match detail page and extracts:
      - Per-map scores and half-scores -> map_results table
      - mapstatsid links -> checkpoints/mapstats_ids.json (legacy handoff)
    """

    MAP_DETAIL_KEY = "match_detail"

    async def run(self) -> dict:
        logger.info(f"[{self.name}] Starting...")
        stats = {"processed": 0, "inserted": 0, "skipped": 0, "errors": 0}

        # Load match IDs from DB (fall back to checkpoint file for legacy runs)
        match_ids = await self._load_match_ids()
        if not match_ids:
            logger.warning(f"[{self.name}] No match IDs found in DB or legacy checkpoint.")
            return stats

        # Pre-load existing match IDs already in map_results
        existing_ids = await self.db.get_all_ids("map_results", "match_id")
        done_set = self.checkpoint.get_done_set(self.MAP_DETAIL_KEY)

        # Load existing mapstats IDs
        mapstats_entries = self._load_mapstats_entries()
        existing_mapstats_ids = {str(e.get("mapstats_id")) for e in mapstats_entries}

        total = len(match_ids)
        logger.info(f"[{self.name}] {total} match IDs to process.")

        for i, match_id in enumerate(match_ids):
            if match_id in existing_ids or str(match_id) in done_set:
                stats["skipped"] += 1
                continue

            # Fetch the match page - need the slug from DB first
            match_row = await self._get_match_row(match_id)
            if match_row:
                url = (
                    match_row.get("hltv_url") or f"{BASE_URL}/matches/{match_id}/match"
                )
            else:
                url = f"{BASE_URL}/matches/{match_id}/match"

            soup = await self.fetch(url)
            if soup is None:
                stats["errors"] += 1
                continue

            # Extract map results
            map_rows, new_mapstats = self._parse_match_detail(soup, match_id)

            write_failed = False
            if map_rows:
                inserted = await self.db.bulk_insert(
                    "map_results", map_rows, replace=True
                )
                stats["inserted"] += inserted
                if inserted < 0:
                    write_failed = True
                    stats["errors"] += 1
                    logger.warning(
                        f"[{self.name}] DB write failed for match_id={match_id}; leaving pending."
                    )

            # Accumulate mapstats entries
            for entry in new_mapstats:
                key = str(entry["mapstats_id"])
                if key not in existing_mapstats_ids:
                    mapstats_entries.append(entry)
                    existing_mapstats_ids.add(key)

            if map_rows and not write_failed:
                self.checkpoint.mark_done(self.MAP_DETAIL_KEY, match_id)
            stats["processed"] += 1

            if (i + 1) % 50 == 0:
                self.log_progress(i + 1, total)
                self._save_mapstats_entries(mapstats_entries)

        self._save_mapstats_entries(mapstats_entries)
        self.checkpoint.save()

        logger.info(
            f"[{self.name}] Done. processed={stats['processed']} inserted={stats['inserted']} "
            f"skipped={stats['skipped']} errors={stats['errors']}"
        )
        return stats

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_match_detail(self, soup: BeautifulSoup, match_id: int) -> tuple[list[dict], list[dict]]:
        """Parse map results and mapstats links from a match detail page."""
        map_rows = []
        mapstats_entries = []

        team1_id, team2_id = self._extract_match_team_ids(soup)

        # Find the maps played section
        map_holder = soup.select_one("div.maps")
        if not map_holder:
            return map_rows, mapstats_entries

        map_divs = map_holder.select("div.mapholder")

        for map_num, map_div in enumerate(map_divs, start=1):
            try:
                # Map name
                map_name_el = map_div.select_one("div.mapname, div.played .mapname")
                map_name = self.safe_text(map_name_el) or "unknown"

                # Was it played? Check for score
                result_el = map_div.select_one("div.results")
                if not result_el:
                    continue  # Map not played (e.g. bo3 ended 2-0)

                # Scores
                team_scores = map_div.select("div.results-team-score")
                team1_score, team2_score = None, None
                if len(team_scores) >= 2:
                    team1_score = self.parse_int(self.safe_text(team_scores[0]))
                    team2_score = self.parse_int(self.safe_text(team_scores[1]))

                # Half scores (CT/T)
                t1_ct, t1_t, t2_ct, t2_t = None, None, None, None
                ct_scores = map_div.select("span.ct")
                t_scores = map_div.select("span.t")
                if len(ct_scores) >= 2:
                    t1_ct = self.parse_int(self.safe_text(ct_scores[0]))
                    t2_ct = self.parse_int(self.safe_text(ct_scores[1]))
                if len(t_scores) >= 2:
                    t1_t = self.parse_int(self.safe_text(t_scores[0]))
                    t2_t = self.parse_int(self.safe_text(t_scores[1]))

                # Winner selector logic:
                # - Use map-local score cells: div.results-team-score
                # - Winning side has class 'won'
                # - First cell maps to team1_id, second maps to team2_id
                winner_id = None
                if len(team_scores) >= 2:
                    cell1_classes = team_scores[0].get("class", [])
                    cell2_classes = team_scores[1].get("class", [])
                    if "won" in cell1_classes:
                        winner_id = team1_id
                    elif "won" in cell2_classes:
                        winner_id = team2_id
                    elif team1_score is not None and team2_score is not None:
                        # Fallback by numeric score if class marker is missing.
                        if team1_score > team2_score:
                            winner_id = team1_id
                        elif team2_score > team1_score:
                            winner_id = team2_id

                map_rows.append(
                    {
                        "match_id": match_id,
                        "map_number": map_num,
                        "map_name": map_name,
                        "team1_score": team1_score,
                        "team2_score": team2_score,
                        "team1_ct_score": t1_ct,
                        "team1_t_score": t1_t,
                        "team2_ct_score": t2_ct,
                        "team2_t_score": t2_t,
                        "winner_id": winner_id,
                    }
                )

                # mapstats link
                stats_link = map_div.select_one("a[href*='/stats/matches/mapstatsid/']")
                if stats_link:
                    href = stats_link.get("href", "")
                    mapstats_id = self._extract_mapstats_id(href)
                    if mapstats_id:
                        mapstats_entries.append(
                            {
                                "match_id": match_id,
                                "map_number": map_num,
                                "map_name": map_name,
                                "mapstats_id": mapstats_id,
                                "mapstats_url": BASE_URL + href
                                if href.startswith("/")
                                else href,
                            }
                        )

            except Exception as e:
                logger.debug(
                    f"[{self.name}] Error parsing map {map_num} for match {match_id}: {e}"
                )
                continue

        return map_rows, mapstats_entries

    def _extract_match_team_ids(self, soup) -> tuple[int | None, int | None]:
        """Extract team1/team2 IDs from the match page header/lineup links."""
        team_links = soup.select(
            "div.team1-gradient a[href*='/team/'], "
            "div.team2-gradient a[href*='/team/'], "
            "div.team a[href*='/team/'], "
            "a.teamName[href*='/team/']"
        )

        team_ids: list[int] = []
        for link in team_links:
            tid = self.extract_id_from_url(link.get("href", ""), -2)
            if tid and tid not in team_ids:
                team_ids.append(tid)
            if len(team_ids) >= 2:
                break

        if len(team_ids) >= 2:
            return team_ids[0], team_ids[1]
        if len(team_ids) == 1:
            return team_ids[0], None
        return None, None

    def _extract_mapstats_id(self, href: str) -> int | None:
        """Extract mapstatsid integer from a URL like /stats/matches/mapstatsid/123456/slug."""
        match = re.search(r"/mapstatsid/(\d+)/", href)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        return None

    # ------------------------------------------------------------------
    # I/O helpers
    # ------------------------------------------------------------------

    async def _get_match_row(self, match_id: int) -> dict | None:
        """Look up a match row from the DB to get its URL."""
        rows = await self.db.execute(
            "SELECT hltv_url FROM matches WHERE match_id = ?", [match_id]
        )
        if rows:
            return {"hltv_url": rows[0][0]}
        return None

    async def _load_match_ids(self) -> list[int]:
        ids = await self.db.get_all_ids("matches", "match_id")
        if ids:
            return sorted(int(x) for x in ids)

        path = os.path.join(CHECKPOINT_DIR, "all_match_ids.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [int(x) for x in json.load(f)]

    def _load_mapstats_entries(self) -> list[dict]:
        path = os.path.join(CHECKPOINT_DIR, "mapstats_ids.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _save_mapstats_entries(self, entries: list[dict]) -> None:
        path = os.path.join(CHECKPOINT_DIR, "mapstats_ids.json")
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f)
        logger.debug(f"[{self.name}] Saved {len(entries)} mapstats entries.")
