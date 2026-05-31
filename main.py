"""
main.py — CLI entrypoint for the structural data extraction pipeline.

Usage:
  python main.py scrape --all
  python main.py scrape --matches
  python main.py scrape --players
  python main.py scrape --teams
  python main.py scrape --events
  python main.py scrape --rankings
  python main.py scrape --news
  python main.py scrape --match-url <base_url>/matches/<id>/<slug>
  python main.py export --csv
  python main.py export --table TABLE_NAME
  python main.py status
"""

import argparse
import asyncio
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

import config
from core.browser import BrowserManager
from core.checkpoint import Checkpoint
from core.exporter import Exporter
from db.database import Database

# All tables for status display
ALL_TABLES = [
    "matches",
    "map_results",
    "player_match_stats",
    "players",
    "player_career_stats",
    "player_event_stats",
    "player_map_stats",
    "teams",
    "team_stats",
    "team_map_stats",
    "roster_history",
    "events",
    "event_teams",
    "world_rankings",
    "player_rankings",
    "news",
]

# Global state
_db: Database | None = None
_browser: BrowserManager | None = None
_checkpoint: Checkpoint | None = None


# Graceful shutdown


def _signal_handler():
    logger.warning("Received shutdown signal. Cancelling tasks...")
    raise SystemExit(0)


def _install_signal_handlers() -> None:
    """Install signal handlers using asyncio when possible (Unix),
    falling back to plain signal.signal on Windows."""
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)
    except (NotImplementedError, RuntimeError):
        # Windows or no running loop yet — fall back to sync handlers
        def _sync_handler(sig, frame):
            logger.warning(f"Received signal {sig}. Shutting down...")
            sys.exit(0)

        signal.signal(signal.SIGINT, _sync_handler)
        signal.signal(signal.SIGTERM, _sync_handler)


# Setup


def _setup_logging():
    log_path = Path(config.LOG_DIR) / "scraper.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )
    logger.add(
        str(log_path),
        level="DEBUG",
        rotation="50 MB",
        retention="30 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
    )


def _create_dirs():
    for d in (config.CHECKPOINT_DIR, config.LOG_DIR, config.EXPORT_DIR):
        Path(d).mkdir(parents=True, exist_ok=True)


async def _startup() -> tuple[Database, BrowserManager, Checkpoint]:
    global _db, _browser, _checkpoint

    _create_dirs()
    _setup_logging()

    logger.info("=" * 60)
    logger.info("Data Extraction Pipeline — Starting up")
    logger.info(f"  DB path:        {config.DB_PATH}")
    logger.info(f"  Checkpoint dir: {config.CHECKPOINT_DIR}")
    logger.info(f"  Log dir:        {config.LOG_DIR}")
    logger.info("=" * 60)

    # Database
    db = Database(config.DB_PATH)
    await db.connect()
    await db.init_db()
    _db = db

    # Checkpoint
    checkpoint = Checkpoint()
    checkpoint.load()
    _checkpoint = checkpoint

    # Browser
    browser = BrowserManager()
    _browser = browser

    # Log DB row counts
    logger.info("Current DB row counts:")
    for table in ALL_TABLES:
        count = await db.row_count(table)
        logger.info(f"  {table:<30} {count:>12,}")

    return db, browser, checkpoint


# Scraper runner


async def _run_scraper(scraper_cls, db, browser, checkpoint) -> dict:
    """Instantiate and run a single scraper, returning its summary."""
    scraper = scraper_cls(db, browser, checkpoint)
    start = time.time()
    logger.info(f"\n{'=' * 60}")
    logger.info(f"Starting: {scraper_cls.__name__}")
    logger.info(f"{'=' * 60}")

    stats = await scraper.run()

    elapsed = time.time() - start
    elapsed_str = _format_elapsed(elapsed)

    # Summary
    print(f"\n  [ {scraper_cls.__name__} ]")
    print(f"    processed : {stats.get('processed', 0):>10,}")
    print(f"    inserted  : {stats.get('inserted', 0):>10,}")
    print(f"    skipped   : {stats.get('skipped', 0):>10,}")
    print(f"    errors    : {stats.get('errors', 0):>10,}")
    print(f"    elapsed   : {elapsed_str:>10}\n")

    return stats


def _format_elapsed(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# Commands


async def cmd_scrape(args, db, browser, checkpoint):
    from scrapers.event_detail import EventDetailScraper
    from scrapers.events import EventsScraper
    from scrapers.match_detail import MatchDetailScraper
    from scrapers.match_stats import MatchStatsScraper
    from scrapers.matches import MatchesScraper
    from scrapers.news import NewsScraper
    from scrapers.player_rankings import PlayerRankingsScraper
    from scrapers.player_stats import PlayerStatsScraper
    from scrapers.players import PlayersScraper
    from scrapers.rankings import RankingsScraper
    from scrapers.roster_history import RosterHistoryScraper
    from scrapers.team_stats import TeamStatsScraper
    from scrapers.teams import TeamsScraper

    # Launch browser only when needed
    await browser.launch()

    if args.match_url:
        await cmd_scrape_single_match(args.match_url, db, browser, checkpoint)
        return

    # Map of --flag to scraper class(es)
    scraper_map = {
        "matches": [MatchesScraper, MatchDetailScraper, MatchStatsScraper],
        "players": [PlayersScraper, PlayerStatsScraper],
        "teams": [TeamsScraper, TeamStatsScraper, RosterHistoryScraper],
        "events": [EventsScraper, EventDetailScraper],
        "rankings": [RankingsScraper, PlayerRankingsScraper],
        "news": [NewsScraper],
    }

    # Full ordered list for --all
    all_scrapers = [
        EventsScraper,
        TeamsScraper,
        PlayersScraper,
        MatchesScraper,
        MatchDetailScraper,
        MatchStatsScraper,
        TeamStatsScraper,
        PlayerStatsScraper,
        RosterHistoryScraper,
        EventDetailScraper,
        RankingsScraper,
        PlayerRankingsScraper,
        NewsScraper,
    ]

    # Determine which scrapers to run
    if args.all:
        to_run = all_scrapers
    else:
        to_run = []
        for flag, classes in scraper_map.items():
            if getattr(args, flag, False):
                to_run.extend(classes)

    if not to_run:
        print("No scrape target specified. Use --all, --match-url, or a specific flag.")
        print("Run: python main.py scrape --help")
        return

    overall_start = time.time()
    all_stats = []
    for cls in to_run:
        s = await _run_scraper(cls, db, browser, checkpoint)
        all_stats.append((cls.__name__, s))

    total_elapsed = _format_elapsed(time.time() - overall_start)

    # Final summary table
    print("\n" + "=" * 72)
    print(
        f"{'SCRAPER':<35} {'PROCESSED':>10} {'INSERTED':>10} {'SKIPPED':>8} {'ERRORS':>7}"
    )
    print("=" * 72)
    for name, s in all_stats:
        print(
            f"{name:<35} {s.get('processed', 0):>10,} {s.get('inserted', 0):>10,} "
            f"{s.get('skipped', 0):>8,} {s.get('errors', 0):>7,}"
        )
    print("=" * 72)
    print(f"Total elapsed: {total_elapsed}\n")


async def cmd_scrape_single_match(match_url: str, db, browser, checkpoint):
    """Fetch and save one match (detail + stats) by URL only."""
    from scrapers.match_detail import MatchDetailScraper
    from scrapers.match_stats import MatchStatsScraper

    match_url = match_url.strip()
    if not match_url:
        print("Missing --match-url value")
        return

    mid_match = re.search(r"/matches/(\d+)/", match_url)
    if not mid_match:
        print(
            "Invalid match URL. Expected format: <base_url>/matches/<id>/<slug>"
        )
        return

    match_id = int(mid_match.group(1))

    # Ensure parent row exists for FK relations before detail/stat inserts.
    existing = await db.execute(
        "SELECT match_id FROM matches WHERE match_id = ?", [match_id]
    )
    if not existing:
        await db.insert_or_ignore(
            "matches",
            {
                "match_id": match_id,
                "status": "completed",
                "hltv_url": match_url,
                "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )

    detail_scraper = MatchDetailScraper(db, browser, checkpoint)
    stats_scraper = MatchStatsScraper(db, browser, checkpoint)

    soup = await detail_scraper.fetch(match_url)
    if soup is None:
        print(f"Failed to fetch match page: {match_url}")
        return

    map_rows, mapstats_entries = detail_scraper._parse_match_detail(soup, match_id)
    if map_rows:
        await db.bulk_insert("map_results", map_rows, replace=True)

    all_rows = []
    for entry in mapstats_entries:
        url = entry.get("mapstats_url")
        if not url:
            continue

        map_number = entry.get("map_number", 1)
        map_name = entry.get("map_name", "unknown")

        base_soup = await stats_scraper.fetch(url)
        if not base_soup:
            continue

        all_rows.extend(
            stats_scraper._parse_scoreboard(
                base_soup, match_id, map_number, map_name, side="overall"
            )
        )

        ct_url = url + "?side=ct" if "?" not in url else url + "&side=ct"
        ct_soup = await stats_scraper.fetch(ct_url)
        if ct_soup:
            all_rows.extend(
                stats_scraper._parse_scoreboard(
                    ct_soup, match_id, map_number, map_name, side="ct"
                )
            )

        t_url = url + "?side=t" if "?" not in url else url + "&side=t"
        t_soup = await stats_scraper.fetch(t_url)
        if t_soup:
            all_rows.extend(
                stats_scraper._parse_scoreboard(
                    t_soup, match_id, map_number, map_name, side="t"
                )
            )

    if all_rows:
        await db.bulk_insert("player_match_stats", all_rows, replace=True)

    print("\nSingle match scrape complete:")
    print(f"  match_id: {match_id}")
    print(f"  map_results rows: {len(map_rows)}")
    print(f"  player_match_stats rows: {len(all_rows)}")


async def cmd_export(args, db):
    exporter = Exporter(db_path=config.DB_PATH, export_dir=config.EXPORT_DIR)
    if args.table:
        if args.table not in ALL_TABLES:
            print(f"Invalid table '{args.table}'.")
            print(f"Allowed tables: {', '.join(ALL_TABLES)}")
            return
        if args.json:
            await asyncio.to_thread(exporter.export_table_json, args.table)
        else:
            await asyncio.to_thread(exporter.export_table, args.table)
    elif args.csv:
        await asyncio.to_thread(exporter.export_all)
    elif args.json:
        await asyncio.to_thread(exporter.export_all_json)
    else:
        print("Specify --csv, --json, or --table TABLE_NAME")


async def cmd_status(db):
    print("\n" + "=" * 50)
    print(f"{'TABLE':<30} {'ROW COUNT':>12}")
    print("=" * 50)
    total = 0
    for table in ALL_TABLES:
        count = await db.row_count(table)
        print(f"{table:<30} {count:>12,}")
        total += count
    print("=" * 50)
    print(f"{'TOTAL':<30} {total:>12,}")
    print()


# Main entry


async def _main():
    parser = argparse.ArgumentParser(
        description="Structural data extraction pipeline — automated DOM parsing and resilient data harvesting.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    # scrape
    scrape_p = subparsers.add_parser("scrape", help="Run scrapers")
    scrape_p.add_argument(
        "--all", action="store_true", help="Run all scrapers in order"
    )
    scrape_p.add_argument(
        "--matches", action="store_true", help="Scrape matches + map stats"
    )
    scrape_p.add_argument(
        "--players", action="store_true", help="Scrape players + player stats"
    )
    scrape_p.add_argument(
        "--teams", action="store_true", help="Scrape teams + team stats + rosters"
    )
    scrape_p.add_argument(
        "--events", action="store_true", help="Scrape events + event details"
    )
    scrape_p.add_argument(
        "--rankings", action="store_true", help="Scrape world + player rankings"
    )
    scrape_p.add_argument("--news", action="store_true", help="Scrape news archive")
    scrape_p.add_argument(
        "--match-url",
        metavar="MATCH_URL",
        help="Scrape one match by URL (detail + stats only)",
    )

    # export
    export_p = subparsers.add_parser("export", help="Export DB tables to CSV/JSON")
    export_p.add_argument("--csv", action="store_true", help="Export ALL tables to CSV")
    export_p.add_argument("--json", action="store_true", help="Export ALL tables to JSON")
    export_p.add_argument(
        "--table", metavar="TABLE_NAME", help="Export a specific table"
    )

    # status
    subparsers.add_parser("status", help="Print row counts for all tables")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    db, browser, checkpoint = await _startup()
    _install_signal_handlers()

    try:
        if args.command == "scrape":
            await cmd_scrape(args, db, browser, checkpoint)
        elif args.command == "export":
            await cmd_export(args, db)
        elif args.command == "status":
            await cmd_status(db)
    finally:
        checkpoint.save()
        await browser.close()
        await db.close()


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
