"""Fleet match-level snapshot export for hltv-scraper."""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

REPO_SLUG = "hltv"
GAME = "Counter-Strike 2"
SCHEMA_VERSION = "1.0"

# Column order fixed for this repo's snapshot.
COLUMNS = [
    "match_id",
    "match_date",
    "team_a",
    "team_b",
    "winner",
    "source_url",
    "status",
    "score_a",
    "score_b",
    "event_name",
    "format",
    "raw_status",
]

STATUS_MAP = {
    "completed": "completed",
    "complete": "completed",
    "finished": "completed",
    "live": "live",
    "ongoing": "live",
    "upcoming": "scheduled",
    "scheduled": "scheduled",
    "notstarted": "scheduled",
    "canceled": "canceled",
    "cancelled": "canceled",
    "postponed": "postponed",
}

_stats = {
    "status_mapped": 0,
    "status_heuristic": 0,
    "date_status_anomaly": 0,
    "dropped_no_teams": 0,
    "rows_out": 0,
}


def _reset_stats() -> None:
    for k in _stats:
        _stats[k] = 0


def _normalize_status(raw: str | None, score_a: int | None, score_b: int | None) -> str:
    key = (raw or "").strip().lower()
    if key in STATUS_MAP:
        _stats["status_mapped"] += 1
        return STATUS_MAP[key]
    _stats["status_heuristic"] += 1
    if score_a is not None or score_b is not None:
        return "completed"
    return "scheduled"


def _parse_match_date(
    date_str: str | None,
    ts: int | float | None,
    *,
    status: str,
    has_scores: bool,
) -> str | None:
    """HLTV: `date` is YYYY-MM-DD string; `timestamp` is Unix seconds when set."""
    next_year = datetime.now(UTC).year + 1
    parsed: datetime | None = None
    used_completed_field = False

    if date_str:
        s = str(date_str).strip()
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y"):
            try:
                parsed = datetime.strptime(s[:19], fmt).replace(tzinfo=UTC)
                used_completed_field = True  # primary play date field
                break
            except ValueError:
                continue
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                used_completed_field = True
            except ValueError:
                parsed = None

    if parsed is None and ts is not None:
        try:
            tsv = float(ts)
        except (TypeError, ValueError):
            tsv = None
        if tsv is not None:
            # Explicit: HLTV match timestamps are Unix seconds (not ms).
            if tsv > 1e12:
                logger.warning("HLTV timestamp looks like ms ({}), treating as ms", tsv)
                parsed = datetime.fromtimestamp(tsv / 1000.0, tz=UTC)
            else:
                parsed = datetime.fromtimestamp(tsv, tz=UTC)

    if parsed is None:
        return None

    year = parsed.year
    # String dates: allow full HLTV history from 2000; unix-derived: sanity 2015..next_year
    if used_completed_field:
        if year < 2000 or year > next_year:
            logger.warning("HLTV date out of bounds: {}", parsed.isoformat())
            return None
    else:
        if year < 2015 or year > next_year:
            logger.warning("HLTV unix date out of bounds (possible unit error): {}", parsed.isoformat())
            return None

    out = parsed.date().isoformat()
    if used_completed_field and status == "scheduled" and not has_scores:
        _stats["date_status_anomaly"] += 1
        logger.warning(
            "date/status anomaly: match_date from play field but status=scheduled and no scores"
        )
    return out


def _winner(
    team_a: str | None,
    team_b: str | None,
    winner_id: int | None,
    team1_id: int | None,
    team2_id: int | None,
    score_a: int | None,
    score_b: int | None,
) -> str | None:
    """Prefer winner_id; else derive from scores. Must equal team_a or team_b exactly."""
    w: str | None = None
    if winner_id is not None:
        if team1_id is not None and winner_id == team1_id:
            w = team_a
        elif team2_id is not None and winner_id == team2_id:
            w = team_b
    if w is None and score_a is not None and score_b is not None and team_a and team_b:
        if score_a > score_b:
            w = team_a
        elif score_b > score_a:
            w = team_b
    if w is not None and w not in (team_a, team_b):
        return None
    return w


def build_rows(db_path: str | Path) -> list[dict[str, Any]]:
    _reset_stats()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT match_id, date, timestamp, team1_id, team2_id, team1_name, team2_name,
               team1_score, team2_score, winner_id, format, event_name, status, hltv_url
        FROM matches
        """
    )
    rows_out: list[dict[str, Any]] = []
    for r in cur:
        team_a = (r["team1_name"] or "").strip() or None
        team_b = (r["team2_name"] or "").strip() or None
        if not team_a and not team_b:
            _stats["dropped_no_teams"] += 1
            continue

        score_a = r["team1_score"]
        score_b = r["team2_score"]
        if score_a is not None:
            score_a = int(score_a)
        if score_b is not None:
            score_b = int(score_b)

        raw_status = r["status"]
        status = _normalize_status(raw_status, score_a, score_b)
        has_scores = score_a is not None or score_b is not None
        match_date = _parse_match_date(r["date"], r["timestamp"], status=status, has_scores=has_scores)

        native_id = r["match_id"]
        winner = _winner(team_a, team_b, r["winner_id"], r["team1_id"], r["team2_id"], score_a, score_b)

        source_url = r["hltv_url"] or None
        # Do not invent slug-based URLs; only use stored URL.

        rows_out.append(
            {
                "match_id": f"{REPO_SLUG}:{native_id}",
                "match_date": match_date,
                "team_a": team_a,
                "team_b": team_b,
                "winner": winner,
                "source_url": source_url,
                "status": status,
                "score_a": score_a,
                "score_b": score_b,
                "event_name": r["event_name"],
                "format": r["format"],
                "raw_status": raw_status,
            }
        )
    conn.close()
    _stats["rows_out"] = len(rows_out)
    return rows_out


def write_snapshot(db_path: str | Path, out_dir: str | Path) -> dict[str, Any]:
    from snapshot_io import write_snapshot_files

    rows = build_rows(db_path)
    if not rows:
        logger.warning("snapshot empty after filters")
    manifest = write_snapshot_files(
        out_dir=Path(out_dir),
        rows=rows,
        columns=COLUMNS,
        source=REPO_SLUG,
        game=GAME,
        schema_version=SCHEMA_VERSION,
        extra_manifest={"stats": dict(_stats)},
    )
    logger.info(
        "snapshot wrote {} rows -> {} (mapped={} heuristic={} dropped_teams={})",
        len(rows),
        out_dir,
        _stats["status_mapped"],
        _stats["status_heuristic"],
        _stats["dropped_no_teams"],
    )
    return manifest
