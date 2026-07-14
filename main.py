"""
hltv-scraper CLI — CS2 esports extraction from HLTV.org.

Usage:
  hltv-scraper scrape --all
  hltv-scraper scrape --matches
  hltv-scraper export --csv
  hltv-scraper status
"""

from __future__ import annotations

import asyncio
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import typer

import config
from cli_ui import (
    configure_rich_logging,
    console,
    end_summary_table,
    scrape_progress,
    startup_panel,
    status_table,
    timed_run,
)
from core.browser import BrowserManager
from core.checkpoint import Checkpoint
from core.exporter import Exporter
from db.database import Database
from loguru import logger

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

app = typer.Typer(
    name="hltv-scraper",
    help="HLTV.org CS2 esports scraper — browser-paced extraction to SQLite.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)
scrape_app = typer.Typer(
    help="Run one or more domain scrapers.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(scrape_app, name="scrape")


def _signal_handler() -> None:
    logger.warning("Received shutdown signal. Cancelling tasks...")
    raise SystemExit(0)


def _install_signal_handlers() -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)
    except (NotImplementedError, RuntimeError):

        def _sync_handler(sig, frame):  # type: ignore[no-untyped-def]
            logger.warning(f"Received signal {sig}. Shutting down...")
            sys.exit(0)

        signal.signal(signal.SIGINT, _sync_handler)
        signal.signal(signal.SIGTERM, _sync_handler)


def _create_dirs() -> None:
    for d in (config.CHECKPOINT_DIR, config.LOG_DIR, config.EXPORT_DIR):
        Path(d).mkdir(parents=True, exist_ok=True)


async def _startup() -> tuple[Database, BrowserManager, Checkpoint]:
    _create_dirs()
    configure_rich_logging("INFO", Path(config.LOG_DIR) / "scraper.log")

    startup_panel(
        title="hltv-scraper · run config",
        rows={
            "Base URL": config.BASE_URL,
            "DB path": config.DB_PATH,
            "Export dir": config.EXPORT_DIR,
            "Browser": config.BROWSER_BACKEND,
            "Rate limit": f"{config.MIN_DELAY}–{config.MAX_DELAY}s + batch pauses",
            "Output format": "csv / json (on export)",
            "Headless": config.HEADLESS,
        },
    )

    db = Database(config.DB_PATH)
    await db.connect()
    await db.init_db()

    checkpoint = Checkpoint()
    checkpoint.load()

    browser = BrowserManager()

    counts = {table: await db.row_count(table) for table in ALL_TABLES}
    logger.info("DB ready · {} tables · {} total rows", len(ALL_TABLES), sum(counts.values()))
    return db, browser, checkpoint


def _format_elapsed(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


async def _run_scraper(scraper_cls, db, browser, checkpoint) -> dict[str, Any]:
    scraper = scraper_cls(db, browser, checkpoint)
    start = time.time()
    logger.info("Starting {}", scraper_cls.__name__)
    stats = await scraper.run()
    stats = stats or {}
    stats["_elapsed"] = time.time() - start
    return stats


async def _scrape_pipeline(
    *,
    run_all: bool,
    matches: bool,
    players: bool,
    teams: bool,
    events: bool,
    rankings: bool,
    news: bool,
    match_url: str | None,
) -> None:
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

    db, browser, checkpoint = await _startup()
    _install_signal_handlers()

    try:
        await browser.launch()

        if match_url:
            await _scrape_single_match(match_url, db, browser, checkpoint)
            return

        scraper_map = {
            "matches": [MatchesScraper, MatchDetailScraper, MatchStatsScraper],
            "players": [PlayersScraper, PlayerStatsScraper],
            "teams": [TeamsScraper, TeamStatsScraper, RosterHistoryScraper],
            "events": [EventsScraper, EventDetailScraper],
            "rankings": [RankingsScraper, PlayerRankingsScraper],
            "news": [NewsScraper],
        }
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

        flags = {
            "matches": matches,
            "players": players,
            "teams": teams,
            "events": events,
            "rankings": rankings,
            "news": news,
        }
        if run_all:
            to_run = all_scrapers
        else:
            to_run = []
            for flag, classes in scraper_map.items():
                if flags.get(flag):
                    to_run.extend(classes)

        if not to_run:
            console.print(
                "[yellow]No scrape target specified.[/] Use [bold]--all[/], [bold]--match-url[/], "
                "or a domain flag. See [bold]hltv-scraper scrape --help[/]."
            )
            raise typer.Exit(1)

        overall_start = time.time()
        all_stats: list[tuple[str, dict[str, Any]]] = []
        with scrape_progress() as progress:
            task = progress.add_task("hltv scrapers", total=len(to_run))
            for cls in to_run:
                progress.update(task, description=f"Running {cls.__name__}")
                stats = await _run_scraper(cls, db, browser, checkpoint)
                all_stats.append((cls.__name__, stats))
                progress.advance(task)

        duration = time.time() - overall_start
        total_processed = sum(s.get("processed", 0) for _, s in all_stats)
        total_inserted = sum(s.get("inserted", 0) for _, s in all_stats)
        total_skipped = sum(s.get("skipped", 0) for _, s in all_stats)
        total_errors = sum(s.get("errors", 0) for _, s in all_stats)

        rows = [
            (name, f"proc={s.get('processed', 0)} ins={s.get('inserted', 0)} "
                   f"skip={s.get('skipped', 0)} err={s.get('errors', 0)}")
            for name, s in all_stats
        ]
        rows.extend(
            [
                ("TOTAL processed", f"{total_processed:,}"),
                ("TOTAL inserted", f"{total_inserted:,}"),
                ("TOTAL skipped", f"{total_skipped:,}"),
                ("TOTAL errors", f"{total_errors:,}"),
            ]
        )
        end_summary_table(title="Scrape summary", rows=rows, duration_s=duration)
    finally:
        checkpoint.save()
        await browser.close()
        await db.close()


async def _scrape_single_match(match_url: str, db, browser, checkpoint) -> None:
    from scrapers.match_detail import MatchDetailScraper
    from scrapers.match_stats import MatchStatsScraper

    match_url = match_url.strip()
    mid_match = re.search(r"/matches/(\d+)/", match_url)
    if not mid_match:
        console.print(
            "[red]Invalid match URL.[/] Expected: "
            f"{config.BASE_URL}/matches/<id>/<slug>"
        )
        raise typer.Exit(1)

    match_id = int(mid_match.group(1))
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

    with scrape_progress() as progress:
        task = progress.add_task(f"match {match_id}", total=None)
        soup = await detail_scraper.fetch(match_url)
        if soup is None:
            console.print(f"[red]Failed to fetch match page:[/] {match_url}")
            raise typer.Exit(1)

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
        progress.update(task, description=f"match {match_id} · done")

    end_summary_table(
        title="Single match scrape",
        rows=[
            ("match_id", match_id),
            ("map_results", len(map_rows)),
            ("player_match_stats", len(all_rows)),
        ],
    )


@scrape_app.callback(invoke_without_command=True)
def scrape(
    ctx: typer.Context,
    run_all: Annotated[bool, typer.Option("--all", help="Run all scrapers in dependency order.")] = False,
    matches: Annotated[bool, typer.Option("--matches", help="Matches + map/player match stats.")] = False,
    players: Annotated[bool, typer.Option("--players", help="Players + player stats.")] = False,
    teams: Annotated[bool, typer.Option("--teams", help="Teams + team stats + roster history.")] = False,
    events: Annotated[bool, typer.Option("--events", help="Events + event detail placements.")] = False,
    rankings: Annotated[bool, typer.Option("--rankings", help="World + player ranking snapshots.")] = False,
    news: Annotated[bool, typer.Option("--news", help="News archive.")] = False,
    match_url: Annotated[
        str | None,
        typer.Option("--match-url", help="Scrape one match by full HLTV URL."),
    ] = None,
) -> None:
    """Run HLTV extractors (browser-backed, rate-limited)."""
    if ctx.invoked_subcommand is not None:
        return
    asyncio.run(
        _scrape_pipeline(
            run_all=run_all,
            matches=matches,
            players=players,
            teams=teams,
            events=events,
            rankings=rankings,
            news=news,
            match_url=match_url,
        )
    )


@app.command("export")
def export(
    csv: Annotated[bool, typer.Option("--csv", help="Export all tables to CSV.")] = False,
    json: Annotated[bool, typer.Option("--json", help="Export all tables to JSON.")] = False,
    table: Annotated[
        str | None,
        typer.Option("--table", help=f"Export one table. One of: {', '.join(ALL_TABLES)}"),
    ] = None,
) -> None:
    """Export SQLite tables to CSV and/or JSON under exports/."""

    async def _run() -> list[str]:
        db, browser, checkpoint = await _startup()
        written: list[str] = []
        try:
            exporter = Exporter(db_path=config.DB_PATH, export_dir=config.EXPORT_DIR)

            def _paths_from_summaries(summaries) -> list[str]:
                out: list[str] = []
                if not summaries:
                    return out
                if isinstance(summaries, dict):
                    summaries = [summaries]
                for item in summaries:
                    if isinstance(item, dict) and item.get("file"):
                        out.append(str(item["file"]))
                    elif item:
                        out.append(str(item))
                return out

            if table:
                if table not in ALL_TABLES:
                    console.print(f"[red]Invalid table[/] '{table}'.")
                    console.print(f"Allowed: {', '.join(ALL_TABLES)}")
                    raise typer.Exit(1)
                if json:
                    summary = await asyncio.to_thread(exporter.export_table_json, table)
                else:
                    summary = await asyncio.to_thread(exporter.export_table, table)
                written.extend(_paths_from_summaries(summary))
            elif csv:
                summaries = await asyncio.to_thread(exporter.export_all)
                written.extend(_paths_from_summaries(summaries))
            elif json:
                summaries = await asyncio.to_thread(exporter.export_all_json)
                written.extend(_paths_from_summaries(summaries))
            else:
                console.print("[yellow]Specify --csv, --json, and/or --table TABLE_NAME[/]")
                raise typer.Exit(1)
        finally:
            checkpoint.save()
            await browser.close()
            await db.close()
        return written

    with timed_run() as elapsed:
        paths = asyncio.run(_run())
    end_summary_table(
        title="Export summary",
        rows=[("Files", len(paths))],
        outputs=paths,
        duration_s=elapsed[0],
    )


@app.command("status")
def status() -> None:
    """Print row counts for every warehouse table."""

    async def _run() -> dict[str, int]:
        db, browser, checkpoint = await _startup()
        try:
            return {table: await db.row_count(table) for table in ALL_TABLES}
        finally:
            checkpoint.save()
            await browser.close()
            await db.close()

    counts = asyncio.run(_run())
    status_table(f"HLTV warehouse · {config.DB_PATH}", counts)


@app.command("snapshot")
def snapshot(
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Directory for data.json/csv/parquet + manifest.json."),
    ] = Path("export"),
) -> None:
    """Write fleet match-level snapshot (data.json/csv/parquet + manifest.json)."""
    from snapshot import write_snapshot

    configure_rich_logging("INFO", Path(config.LOG_DIR) / "scraper.log")
    startup_panel(
        title="hltv-scraper · snapshot",
        rows={
            "DB path": config.DB_PATH,
            "Output dir": out,
            "Grain": "match/series",
            "Files": "data.json, data.csv, data.parquet, manifest.json",
        },
    )
    with timed_run() as elapsed:
        manifest = write_snapshot(config.DB_PATH, out)
    end_summary_table(
        title="Snapshot summary",
        rows=[
            ("Records", manifest.get("record_count")),
            ("Status mapped", manifest.get("stats", {}).get("status_mapped")),
            ("Status heuristic", manifest.get("stats", {}).get("status_heuristic")),
            ("Dropped (no teams)", manifest.get("stats", {}).get("dropped_no_teams")),
        ],
        outputs=[
            out / "manifest.json",
            out / "data.json",
            out / "data.csv",
            out / "data.parquet",
        ],
        duration_s=elapsed[0],
    )


def main() -> None:
    """Console script entrypoint."""
    app()


if __name__ == "__main__":
    main()
