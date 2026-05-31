"""
scrapers/events.py — Scrape all archived and upcoming events.

Saves event IDs to checkpoints/all_event_ids.json for EventDetailScraper.
"""

import json
import os
import re
from datetime import datetime, timezone

from loguru import logger

from bs4 import BeautifulSoup

from config import BASE_URL, CHECKPOINT_DIR
from scrapers.base import BaseScraper


class EventsScraper(BaseScraper):
    """
    Step 1: Paginate /events/archive to collect all past events.
    Step 2: Scrape /events for upcoming events.
    """

    SCRAPER_KEY = "events"

    async def run(self) -> dict:
        logger.info(f"[{self.name}] Starting...")
        stats = {"processed": 0, "inserted": 0, "skipped": 0, "errors": 0}

        existing_ids = await self.db.get_all_ids("events", "event_id")
        done_set = self.checkpoint.get_done_set(self.SCRAPER_KEY)
        all_event_ids: set[int] = set(existing_ids)

        # Step 1 — Archive (paginated)
        offset = 0
        max_consecutive_errors = 5
        consecutive_errors = 0
        while True:
            url = f"{BASE_URL}/events/archive?offset={offset}"
            soup = await self.fetch(url)
            if soup is None:
                stats["errors"] += 1
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(
                        f"[{self.name}] {max_consecutive_errors} consecutive fetch failures. Stopping archive pagination."
                    )
                    break
                offset += 50
                continue
            consecutive_errors = 0

            events = self._parse_event_list(soup, is_completed=1)
            if not events:
                logger.info(f"[{self.name}] No more archive events at offset={offset}.")
                break

            for ev in events:
                eid = ev.get("event_id")
                if not eid:
                    continue
                all_event_ids.add(eid)
                if eid in existing_ids or str(eid) in done_set:
                    stats["skipped"] += 1
                    continue
                inserted = await self.db.insert_or_ignore("events", ev)
                if inserted:
                    stats["inserted"] += 1
                self.checkpoint.mark_done(self.SCRAPER_KEY, eid)
                stats["processed"] += 1

            offset += 50

        # Step 2 — Upcoming events
        url = f"{BASE_URL}/events"
        soup = await self.fetch(url)
        if soup:
            upcoming = self._parse_event_list(soup, is_completed=0)
            for ev in upcoming:
                eid = ev.get("event_id")
                if not eid:
                    continue
                all_event_ids.add(eid)
                if eid in existing_ids:
                    await self.db.upsert("events", ev, "event_id")
                else:
                    inserted = await self.db.insert_or_ignore("events", ev)
                    if inserted:
                        stats["inserted"] += 1
                stats["processed"] += 1

        # Save all event IDs
        self._save_event_ids(all_event_ids)
        self.checkpoint.save()

        logger.info(
            f"[{self.name}] Done. processed={stats['processed']} inserted={stats['inserted']} "
            f"skipped={stats['skipped']} errors={stats['errors']}"
        )
        return stats

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_event_list(self, soup: BeautifulSoup, is_completed: int) -> list[dict]:
        """Parse event rows from an events list page."""
        events = []
        seen_ids: set[int] = set()

        # Layout-agnostic strategy: parse all links that match event detail URLs.
        for link in soup.select("a[href*='/events/']"):
            href = link.get("href", "") or ""
            m = re.search(r"/events/(\d+)/", href)
            if not m:
                continue

            parent = link
            # Try to parse from a richer card/row container first.
            for _ in range(6):
                if parent is None:
                    break
                classes = " ".join(parent.get("class") or [])
                if any(
                    token in classes.lower()
                    for token in (
                        "event",
                        "small-event",
                        "featured-event",
                        "ongoing-event",
                        "big-event",
                        "event-holder",
                    )
                ):
                    break
                parent = getattr(parent, "parent", None)

            ev = self._parse_event_element(parent or link, is_completed)
            if not ev:
                continue

            eid = ev.get("event_id")
            if not eid or eid in seen_ids:
                continue
            seen_ids.add(eid)
            events.append(ev)

        return events

    def _parse_event_element(self, el, is_completed: int) -> dict | None:
        """Extract data from a single event row/card element."""
        try:
            # Get href
            if el.name == "a":
                href = el.get("href", "")
            else:
                link = el.select_one("a[href*='/events/']")
                if not link:
                    return None
                href = link.get("href", "")

            # Robust event-id extraction across relative/absolute forms.
            match = re.search(r"/events/(\d+)/", href)
            event_id = int(match.group(1)) if match else self.extract_id_from_url(href, -2)
            if not event_id:
                return None

            # Name
            name_el = el.select_one(
                "div.big-event-name, span.event-name, td.event-name-col"
            )
            name = self.safe_text(name_el)
            if not name:
                name = self.safe_text(el.select_one("span.name, div.name"))
            if not name and "link" in locals():
                name = self.safe_text(link)

            # Dates
            date_start = None
            date_end = None
            date_el = el.select_one("span.event-date, div.eventdate, td.col-date")
            if date_el:
                date_text = self.safe_text(date_el) or ""
                dates = re.findall(r"\d{4}-\d{2}-\d{2}", date_text)
                if len(dates) >= 2:
                    date_start, date_end = dates[0], dates[1]
                elif len(dates) == 1:
                    date_start = dates[0]

            # Location
            loc_el = el.select_one("span.location, div.location, td.location")
            location = self.safe_text(loc_el)
            country = None
            country_el = el.select_one("img.flag")
            if country_el:
                country = country_el.get("alt") or country_el.get("title")

            # Prize pool
            prize_el = el.select_one("span.prizepool, div.prize-pool, td.prizepool")
            prize_pool = self.safe_text(prize_el)
            prize_pool_usd = self.parse_prize_usd(prize_pool)

            # Number of teams
            teams_el = el.select_one("span.participants, div.num-teams")
            num_teams = self.parse_int(self.safe_text(teams_el))

            # Tier
            tier = None
            for cls in el.get("class") or []:
                if "tier" in cls.lower():
                    tier = cls.replace("-", " ").strip()
                    break
            tier_el = el.select_one("span.tier, div.eventTier")
            if tier_el:
                tier = self.safe_text(tier_el)

            # Type
            event_type = None
            type_el = el.select_one("span.event-type, div.event-type")
            if type_el:
                event_type = self.safe_text(type_el)

            # Logo
            logo_el = el.select_one("img.eventlogo, img.event-logo")
            logo_url = self.safe_attr(logo_el, "src")

            return {
                "event_id": event_id,
                "name": name,
                "date_start": date_start,
                "date_end": date_end,
                "location": location,
                "country": country,
                "prize_pool": prize_pool,
                "prize_pool_usd": prize_pool_usd,
                "num_teams": num_teams,
                "event_type": event_type,
                "tier": tier,
                "hltv_url": BASE_URL + href if href.startswith("/") else href,
                "logo_url": logo_url,
                "is_completed": is_completed,
                "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        except Exception as e:
            logger.debug(f"[{self.name}] Error parsing event element: {e}")
            return None

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def _save_event_ids(self, event_ids: set[int]) -> None:
        path = os.path.join(CHECKPOINT_DIR, "all_event_ids.json")
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump(list(event_ids), f)
        logger.info(f"[{self.name}] Saved {len(event_ids)} event IDs to {path}")
