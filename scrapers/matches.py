"""
scrapers/matches.py — Scrape all match results, upcoming, and live matches.

Saves match_ids to checkpoints/all_match_ids.json for use by downstream scrapers.
"""

import json
import os
from datetime import datetime, timezone

from loguru import logger

from bs4 import BeautifulSoup

from config import BASE_URL, CHECKPOINT_DIR
from scrapers.base import BaseScraper


class MatchesScraper(BaseScraper):
    """
    Scrapes:
      1. All historical results (paginated /results?offset=N)
      2. Upcoming matches (/matches)
      3. Live matches (/matches, class='live')
    """

    SCRAPER_KEY = "matches"

    async def run(self) -> dict:
        logger.info(f"[{self.name}] Starting...")
        stats = {"processed": 0, "inserted": 0, "skipped": 0, "errors": 0}

        # Pre-load existing match IDs from DB
        existing_ids = await self.db.get_all_ids("matches", "match_id")
        all_match_ids = set(existing_ids)

        # Step 1 — Historical results (paginated)
        offset = 0
        page_num = 0
        max_consecutive_errors = 5
        consecutive_errors = 0
        while True:
            url = f"{BASE_URL}/results?offset={offset}"
            soup = await self.fetch(url)
            if soup is None:
                logger.warning(
                    f"[{self.name}] Failed to fetch results page offset={offset}"
                )
                stats["errors"] += 1
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(
                        f"[{self.name}] {max_consecutive_errors} consecutive fetch failures. Stopping results pagination."
                    )
                    break
                offset += 100
                continue
            consecutive_errors = 0

            rows = self._parse_result_rows(soup)
            if not rows:
                logger.info(
                    f"[{self.name}] No more results at offset={offset}. Done with results."
                )
                break

            for row_data in rows:
                mid = row_data.get("match_id")
                if not mid:
                    continue
                all_match_ids.add(mid)
                if mid in existing_ids or self.checkpoint.is_done(
                    self.SCRAPER_KEY, mid
                ):
                    stats["skipped"] += 1
                    continue
                inserted = await self.db.insert_or_ignore("matches", row_data)
                if inserted:
                    stats["inserted"] += 1
                self.checkpoint.mark_done(self.SCRAPER_KEY, mid)
                stats["processed"] += 1

            page_num += 1
            if page_num % 10 == 0:
                self.log_progress(
                    stats["processed"], stats["processed"] + stats["skipped"]
                )
            offset += 100

        # Step 2 — Upcoming + Live matches
        url = f"{BASE_URL}/matches"
        soup = await self.fetch(url)
        if soup:
            upcoming = self._parse_upcoming(soup)
            live = self._parse_live(soup)
            for row_data in upcoming + live:
                mid = row_data.get("match_id")
                if not mid:
                    continue
                all_match_ids.add(mid)
                if mid in existing_ids:
                    # Update status for upcoming/live
                    await self.db.upsert("matches", row_data, "match_id")
                    stats["processed"] += 1
                else:
                    inserted = await self.db.insert_or_ignore("matches", row_data)
                    if inserted:
                        stats["inserted"] += 1
                    stats["processed"] += 1

        # Save all discovered match IDs for downstream scrapers
        self._save_all_match_ids(all_match_ids)
        self.checkpoint.save()

        logger.info(
            f"[{self.name}] Done. processed={stats['processed']} inserted={stats['inserted']} "
            f"skipped={stats['skipped']} errors={stats['errors']}"
        )
        return stats

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_result_rows(self, soup: BeautifulSoup) -> list[dict]:
        """Parse match result rows from the /results page."""
        rows = []
        result_lists = soup.select("div.results-holder div.results-sublist")
        if not result_lists:
            # Fallback: try top-level result divs
            result_lists = [soup]

        for sublist in result_lists:
            # Try to extract date from section header
            section_date = None
            date_el = sublist.select_one(
                "div.standard-headline, span.results-sublist-headline"
            )
            if date_el:
                ts_el = sublist.select_one("[data-zonedgrouping-entry-unix]")
                if ts_el:
                    raw_ts = ts_el.get("data-zonedgrouping-entry-unix")
                    if raw_ts:
                        try:
                            ts_int = int(raw_ts) // 1000
                            section_date = datetime.fromtimestamp(
                                ts_int, tz=timezone.utc
                            ).strftime("%Y-%m-%d")
                        except Exception:
                            pass

            for row in sublist.select("div.result-con, a.a-reset"):
                row_data = self._parse_single_result(row, section_date)
                if row_data:
                    rows.append(row_data)

        return rows

    def _parse_single_result(self, row, fallback_date=None) -> dict | None:
        """
        Extract data from a single match result row element.

        Note: lxml breaks nested <a> tags — HLTV result rows use an outer
        <div class="result-con"> wrapping an <a class="a-reset"> which itself
        may contain inner <a> tags (e.g. for event links). When lxml encounters
        a nested anchor it terminates the outer one, so team names, scores and
        star icons that appear after the inner anchor end up as siblings of the
        outer <a> rather than its children. We therefore always search the
        outermost container (the passed `row` element or its parent) for
        these displaced children, falling back to searching up the DOM tree.
        """
        try:
            # Resolve the anchor and its best search context
            if row.name == "a":
                link = row
                # Search context: try parent first (catches lxml-displaced siblings)
                search_ctx = row.parent or row
            else:
                link = row.select_one("a.a-reset, a[href*='/matches/']")
                search_ctx = row  # row is already the outer container

            if not link:
                return None

            href = link.get("href", "")
            match_id = self.extract_id_from_url(href, position=-2)
            if not match_id:
                return None

            # Timestamp — search whole context
            ts_el = search_ctx.select_one("[data-zonedgrouping-entry-unix]")
            timestamp = None
            date_str = fallback_date
            if ts_el:
                raw_ts = ts_el.get("data-zonedgrouping-entry-unix")
                if raw_ts:
                    try:
                        timestamp = int(raw_ts) // 1000
                        date_str = datetime.fromtimestamp(
                            timestamp, tz=timezone.utc
                        ).strftime("%Y-%m-%d")
                    except Exception:
                        pass

            # Teams — search whole context (may be displaced siblings)
            team_els = search_ctx.select("div.team")
            team1_name = self.safe_text(team_els[0]) if len(team_els) > 0 else None
            team2_name = self.safe_text(team_els[1]) if len(team_els) > 1 else None

            # Team IDs from logo hrefs
            team_logo_links = search_ctx.select("img.teamlogo")
            team1_id, team2_id = None, None
            if len(team_logo_links) > 0:
                parent_a = team_logo_links[0].find_parent("a")
                if parent_a:
                    team1_id = self.extract_id_from_url(parent_a.get("href", ""), -2)
            if len(team_logo_links) > 1:
                parent_a = team_logo_links[1].find_parent("a")
                if parent_a:
                    team2_id = self.extract_id_from_url(parent_a.get("href", ""), -2)

            # Scores — search whole context
            score_el = search_ctx.select_one(
                "td.result-score, span.result-score, div.result-score"
            )
            team1_score, team2_score, winner_id = None, None, None
            if score_el:
                score_spans = score_el.select("span")
                if len(score_spans) >= 2:
                    team1_score = self.parse_int(score_spans[0].get_text(strip=True))
                    team2_score = self.parse_int(score_spans[1].get_text(strip=True))
                    if team1_score is not None and team2_score is not None:
                        if team1_score > team2_score:
                            winner_id = team1_id
                        elif team2_score > team1_score:
                            winner_id = team2_id

            # Event name — can be in the anchor itself (before nested links break it)
            event_el = search_ctx.select_one(
                "td.event span.event-name, span.event-name, div.event-name"
            )
            event_name = self.safe_text(event_el)
            event_id = None
            event_link = search_ctx.select_one(
                "td.event a[href*='/events/'], a[href*='/events/']"
            )
            if event_link:
                event_id = self.extract_id_from_url(event_link.get("href", ""), -2)

            # Format
            format_el = search_ctx.select_one("td.format, div.map-text, span.format")
            match_format = self.safe_text(format_el)

            # Stars — search whole context (often displaced by nested anchors)
            star_els = search_ctx.select("i.fa-star, i[class*='star']")
            stars = len(star_els)

            # LAN
            lan_el = search_ctx.select_one("i.fa-globe, img[src*='lan']")
            lan = 1 if lan_el else 0

            return {
                "match_id": match_id,
                "date": date_str,
                "timestamp": timestamp,
                "team1_id": team1_id,
                "team2_id": team2_id,
                "team1_name": team1_name,
                "team2_name": team2_name,
                "team1_score": team1_score,
                "team2_score": team2_score,
                "winner_id": winner_id,
                "format": match_format,
                "event_id": event_id,
                "event_name": event_name,
                "lan": lan,
                "stars": stars,
                "status": "completed",
                "hltv_url": BASE_URL + href if href.startswith("/") else href,
                "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        except Exception as e:
            logger.debug(f"[{self.name}] Error parsing result row: {e}")
            return None

    def _parse_upcoming(self, soup) -> list[dict]:
        """Parse upcoming matches from the /matches page."""
        matches = []
        for row in soup.select("div.upcomingMatch"):
            data = self._parse_upcoming_row(row, status="upcoming")
            if data:
                matches.append(data)
        return matches

    def _parse_live(self, soup) -> list[dict]:
        """Parse live matches from the /matches page."""
        matches = []
        for row in soup.select("div.liveMatch, div.upcomingMatch.live"):
            data = self._parse_upcoming_row(row, status="live")
            if data:
                matches.append(data)
        return matches

    def _parse_upcoming_row(self, row, status: str) -> dict | None:
        """Parse a single upcoming/live match row."""
        try:
            link = row.select_one("a")
            if not link:
                return None
            href = link.get("href", "")
            match_id = self.extract_id_from_url(href, position=-2)
            if not match_id:
                return None

            # Timestamp
            ts_el = row.select_one("[data-zonedgrouping-entry-unix]")
            timestamp = None
            date_str = None
            if ts_el:
                raw_ts = ts_el.get("data-zonedgrouping-entry-unix")
                if raw_ts:
                    try:
                        timestamp = int(raw_ts) // 1000
                        date_str = datetime.fromtimestamp(
                            timestamp, tz=timezone.utc
                        ).strftime("%Y-%m-%d")
                    except Exception:
                        pass

            team_els = row.select("div.matchTeamName")
            team1_name = self.safe_text(team_els[0]) if len(team_els) > 0 else None
            team2_name = self.safe_text(team_els[1]) if len(team_els) > 1 else None

            event_el = row.select_one("div.matchEventName, span.matchEvent")
            event_name = self.safe_text(event_el)

            format_el = row.select_one("div.matchMeta")
            match_format = self.safe_text(format_el)

            stars_el = row.select("i.fa-star")
            stars = len(stars_el)

            return {
                "match_id": match_id,
                "date": date_str,
                "timestamp": timestamp,
                "team1_id": None,
                "team2_id": None,
                "team1_name": team1_name,
                "team2_name": team2_name,
                "team1_score": None,
                "team2_score": None,
                "winner_id": None,
                "format": match_format,
                "event_id": None,
                "event_name": event_name,
                "lan": 0,
                "stars": stars,
                "status": status,
                "hltv_url": BASE_URL + href if href.startswith("/") else href,
                "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        except Exception as e:
            logger.debug(f"[{self.name}] Error parsing upcoming row: {e}")
            return None

    def _save_all_match_ids(self, match_ids: set) -> None:
        """Save all discovered match IDs to checkpoints/all_match_ids.json."""
        path = os.path.join(CHECKPOINT_DIR, "all_match_ids.json")
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump(list(match_ids), f)
        logger.info(f"[{self.name}] Saved {len(match_ids)} match IDs to {path}")
