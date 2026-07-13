"""Shared Rich CLI chrome for the ark-daemon scraper fleet."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from loguru import logger
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

# Fleet visual language — keep identical across repos.
STYLE_BORDER = "cyan"
STYLE_TITLE = "bold cyan"
STYLE_KEY = "cyan"
STYLE_VALUE = "green"
STYLE_WARN = "yellow"
STYLE_ERR = "red"

console = Console()


def configure_rich_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Console via RichHandler; optional file sink via loguru."""
    level_name = (level or "INFO").upper()
    level_no = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level_no,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                console=console,
                rich_tracebacks=True,
                show_path=False,
                markup=True,
                log_time_format="[%X]",
            )
        ],
        force=True,
    )

    def _loguru_to_logging(message: Any) -> None:
        record = message.record
        name = record["name"] or "scraper"
        try:
            std_level = logger.level(record["level"].name).no
        except Exception:
            std_level = level_no
        # Map loguru levels onto stdlib roughly (loguru uses same major numbers).
        std_level = min(max(int(std_level), logging.DEBUG), logging.CRITICAL)
        logging.getLogger(name).log(std_level, "%s", record["message"])

    logger.remove()
    logger.add(_loguru_to_logging, level=level_name, format="{message}")
    if log_file is not None:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(log_file),
            level="DEBUG",
            rotation="20 MB",
            retention="14 days",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
        )


def startup_panel(
    *,
    title: str,
    rows: Mapping[str, Any],
) -> None:
    """Show active run configuration before work starts."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style=STYLE_KEY, justify="right")
    grid.add_column(style=STYLE_VALUE)
    for key, value in rows.items():
        grid.add_row(f"{key}", str(value))
    console.print(
        Panel(
            grid,
            title=f"[{STYLE_TITLE}]{title}[/]",
            border_style=STYLE_BORDER,
            padding=(1, 2),
        )
    )


def end_summary_table(
    *,
    title: str,
    rows: Iterable[tuple[str, Any]],
    outputs: Iterable[str | Path] | None = None,
    duration_s: float | None = None,
) -> None:
    """End-of-run metrics + optional output paths."""
    table = Table(title=title, border_style=STYLE_BORDER, show_header=True, header_style=STYLE_TITLE)
    table.add_column("Metric", style=STYLE_KEY)
    table.add_column("Value", style=STYLE_VALUE, justify="right")
    for key, value in rows:
        table.add_row(str(key), str(value))
    if duration_s is not None:
        table.add_row("Duration", _fmt_duration(duration_s))
    console.print(table)

    paths = list(outputs or [])
    if paths:
        out = Table(
            title="Output files",
            border_style=STYLE_BORDER,
            show_header=True,
            header_style=STYLE_TITLE,
        )
        out.add_column("#", style="dim", justify="right")
        out.add_column("Path", style=STYLE_VALUE)
        for i, path in enumerate(paths, start=1):
            out.add_row(str(i), str(path))
        console.print(out)


def scrape_progress(*, transient: bool = False) -> Progress:
    """Standard live progress bar used during scrapes."""
    return Progress(
        SpinnerColumn(),
        TextColumn(f"[bold {STYLE_BORDER}]{{task.description}}"),
        BarColumn(bar_width=28),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=transient,
        refresh_per_second=8,
    )


@contextmanager
def timed_run() -> Iterator[list[float]]:
    """Yield a single-element list updated with elapsed seconds on exit."""
    start = time.perf_counter()
    box: list[float] = [0.0]
    try:
        yield box
    finally:
        box[0] = time.perf_counter() - start


def _fmt_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def status_table(title: str, counts: Mapping[str, int]) -> None:
    table = Table(title=title, border_style=STYLE_BORDER, header_style=STYLE_TITLE)
    table.add_column("Table", style=STYLE_KEY)
    table.add_column("Rows", style=STYLE_VALUE, justify="right")
    total = 0
    for name, count in counts.items():
        table.add_row(str(name), f"{int(count):,}")
        total += int(count)
    table.add_row(Text("TOTAL", style="bold"), Text(f"{total:,}", style="bold green"))
    console.print(table)
