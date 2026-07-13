"""
scrapers/player_rankings.py - Scrape the annual HLTV Top 20 player rankings.
"""

import re
from datetime import datetime, timezone
from loguru import logger

from bs4 import BeautifulSoup

from config import BASE_URL
from scrapers.base import BaseScraper

START_YEAR = 2012


class PlayerRankingsScraper(BaseScraper):
    """
    For each year from 2012 to the current year, scrapes the Top 20 Players
    article from HLTV's news section and extracts the annual player rankings.
    """

    SCRAPER_KEY = "player_rankings"

    async def run(self) -> dict:
        logger.info(f"[{self.name}] Starting...")
        stats = {"processed": 0, "inserted": 0, "skipped": 0, "errors": 0}

        current_year = datetime.now(timezone.utc).year
        done_set = self.checkpoint.get_done_set(self.SCRAPER_KEY)

        for year in range(START_YEAR, current_year + 1):
            if str(year) in done_set:
                stats["skipped"] += 1
                continue

            url = f"{BASE_URL}/news/top-20-players-of-{year}"
            soup = await self.fetch(url)
            if soup is None:
                logger.warning(f"[{self.name}] Could not fetch Top 20 for {year}")
                stats["errors"] += 1
                continue

            rows = self._parse_top20(soup, year)
            if rows:
                n = await self.db.bulk_insert("player_rankings", rows, replace=True)
                stats["inserted"] += n
                logger.info(f"[{self.name}] Year {year}: {len(rows)} players inserted.")
            else:
                logger.warning(f"[{self.name}] No ranking data found for {year}")

            self.checkpoint.mark_done(self.SCRAPER_KEY, year)
            stats["processed"] += 1

        self.checkpoint.save()
        logger.info(
            f"[{self.name}] Done. processed={stats['processed']} inserted={stats['inserted']} "
            f"skipped={stats['skipped']} errors={stats['errors']}"
        )
        return stats

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_top20(self, soup: BeautifulSoup, year: int) -> list[dict]:
        """
        Parse the Top 20 players from an annual HLTV news article.

        HLTV formats these as numbered articles with embedded tables or
        structured divs. We try multiple strategies.
        """
        rows = []

        # Strategy 1: structured ranking tables embedded in article
        for table in soup.select("table.ranking-table, table.stats-table"):
            for tr in table.select("tbody tr"):
                row = self._parse_table_row(tr, year)
                if row:
                    rows.append(row)

        if rows:
            return rows

        # Strategy 2: article body with player profile links
        # The article typically has "1. PlayerName" headings or inline rank labels
        article = soup.select_one("div.article-body, div.newsContent, div.body")
        if not article:
            return rows

        # Find all player links and try to associate with rank numbers
        player_links = article.select("a[href*='/player/']")
        seen_ids = set()
        rank = 1

        for link in player_links:
            href = link.get("href", "")
            player_id = self.extract_id_from_url(href, -2)
            if not player_id or player_id in seen_ids:
                continue
            if rank > 20:
                break

            player_name = self.safe_text(link)

            # Try to find team name near the link
            team_name = None
            parent = link.find_parent()
            if parent:
                team_link = parent.select_one("a[href*='/team/']")
                if not team_link:
                    # Look in siblings
                    for sib in parent.next_siblings:
                        if hasattr(sib, "select_one"):
                            team_link = sib.select_one("a[href*='/team/']")
                            if team_link:
                                break
                if team_link:
                    team_name = self.safe_text(team_link)

            # Try to find rating near the link
            rating = None
            surrounding_text = self.safe_text(parent) or ""
            rating_match = re.search(r"[Rr]ating[:\s]+(\d+\.\d+)", surrounding_text)
            if rating_match:
                rating = self.parse_float(rating_match.group(1))

            # Try to infer rank from nearby text (#1, #2, etc.)
            rank_match = re.search(r"#\s*(\d+)", surrounding_text)
            if rank_match:
                inferred_rank = self.parse_int(rank_match.group(1))
                if inferred_rank and 1 <= inferred_rank <= 20:
                    rank = inferred_rank

            rows.append({
                "year": year,
                "rank": rank,
                "player_id": player_id,
                "player_name": player_name,
                "team_name": team_name,
                "rating": rating,
            })
            seen_ids.add(player_id)
            rank += 1

        return rows

    def _parse_table_row(self, tr, year: int) -> dict | None:
        """Parse a ranking table row."""
        try:
            tds = tr.select("td")
            if len(tds) < 2:
                return None

            # Rank
            rank = self.parse_int(self.safe_text(tds[0]))
            if rank is None or not (1 <= rank <= 20):
                return None

            # Player
            player_link = tr.select_one("a[href*='/player/']")
            if not player_link:
                return None
            player_id = self.extract_id_from_url(player_link.get("href", ""), -2)
            player_name = self.safe_text(player_link)

            # Team
            team_link = tr.select_one("a[href*='/team/']")
            team_name = self.safe_text(team_link)

            # Rating
            rating = None
            if len(tds) > 2:
                rating = self.parse_float(self.safe_text(tds[-1]))

            return {
                "year": year,
                "rank": rank,
                "player_id": player_id,
                "player_name": player_name,
                "team_name": team_name,
                "rating": rating,
            }
        except Exception as e:
            logger.debug(f"[{self.name}] Error parsing ranking table row: {e}")
            return None

