# Structural Data Extraction Pipeline

A production-grade, modular Python architecture for automated DOM parsing and structural data extraction from dynamic web platforms. Designed for long-running, resilient data harvesting with built-in checkpointing, human-like request pacing, and deterministic JSON/CSV pipeline output.

---

## Architectural Overview

The system is built around a **multi-stage scraper orchestration layer** that decouples data discovery, structural parsing, and persistence. Each stage is fault-isolated: a failure in one extractor does not cascade to others.

| Stage | Responsibility |
|---|---|
| **Discovery** | Automated DOM traversal and ID enumeration across paginated archives |
| **Detail Extraction** | Deep-page parsing of structured content (scoreboards, rosters, rankings) |
| **Aggregation** | Per-entity statistic rollups and cross-reference linking |
| **Persistence** | ACID-compliant SQLite storage with WAL mode for concurrent reads |
| **Export** | Deterministic CSV/JSON serialization with schema-aware sorting |

---

## Core Capabilities

### Automated DOM Parsing
Every extractor operates on a **layout-agnostic selector strategy**. Instead of brittle XPath anchors, the pipeline uses multi-tier fallback heuristics:

- Semantic class-token matching (`event`, `ranking`, `stats-table`)
- Attribute inference (`data-*` unix timestamps, `href` ID extraction)
- Parent-container bubbling (resilient against nested-anchor DOM fragmentation caused by parser normalization)

This makes the system robust against minor layout shifts, A/B tests, and templating changes.

### Dynamic Error Handling
The browser automation layer is designed for **resilient access** under dynamic server conditions:

- **Automatic challenge-page detection** — identifies and waits out interstitial gates without hardcoded sleeps.
- **Exponential backoff** with jitter on HTTP 429/403/503 responses.
- **Session fingerprint rotation** — periodic browser context restarts to maintain stable session quality.
- **Per-request retry budget** — up to 10 attempts per URL before graceful degradation (logged, not crashed).

### Efficient Pipeline Output
All extracted data is staged in a **normalized SQLite warehouse** before export:

- `INSERT OR IGNORE` / `UPSERT` semantics prevent duplicate ingestion on re-runs.
- Bulk `executemany` operations minimize transaction overhead.
- CSV exports are generated via pandas `read_sql` for memory-efficient streaming of large tables.
- Files are timestamped and written to `exports/` for downstream ETL compatibility.

---

## Requirements

- Python **3.11+**
- Dependencies listed in `requirements.txt`

### Install

```bash
pip install -r requirements.txt
```

### Configure target environment

Copy the example environment file and adjust values:

```bash
cp .env.example .env
```

Edit `.env` to set the base URL of your target data platform and any optional browser backend settings.

> **Note:** `.env` is gitignored by design. Never commit runtime secrets or endpoint configurations.

---

## How to Run

### Full pipeline (recommended first run)

```bash
python main.py scrape --all
```

Runs all extractors in dependency order. Safe to interrupt with `Ctrl+C` — progress is saved automatically.

### Run individual extractors

```bash
python main.py scrape --matches     # match results + map-level breakdowns
python main.py scrape --players     # player profiles + career / event / map stats
python main.py scrape --teams       # team profiles + aggregate stats + roster history
python main.py scrape --events      # event listings + team placements
python main.py scrape --rankings    # world rankings (all weekly snapshots)
python main.py scrape --news        # full news archive
```

### Check progress

```bash
python main.py status
```

Prints a table of row counts for every warehouse table:

```
Table                          Row Count
------------------------------  ----------
matches                             54,231
map_results                        121,445
player_match_stats               2,341,002
...
```

### Export to CSV / JSON

```bash
python main.py export --csv                   # export all tables to CSV
python main.py export --json                  # export all tables to JSON
python main.py export --table matches         # export one specific table to CSV
```

Files are written to `exports/` with today's date in the filename.

---

## Checkpoint / Resume System

Every extractor saves its progress to `checkpoints/state.json` after every 100 items processed. If you stop the pipeline (Ctrl+C or crash), just re-run the same command — it will pick up exactly where it left off.

The database is also used as a source of truth: on startup each extractor pre-loads all existing IDs from the DB so already-ingested items are skipped even if the checkpoint file was deleted.

---

## Rate Limiting & Resilience

The pipeline makes **one request at a time** with human-like delays to minimize server load. There are no proxies by default.

Default settings in `config.py`:

| Setting | Value | Description |
|---|---|---|
| `MIN_DELAY` | 4.0s | Minimum sleep before every request |
| `MAX_DELAY` | 9.0s | Maximum sleep before every request |
| `BATCH_PAUSE_EVERY` | 30 | Take a longer pause every N requests |
| `BATCH_PAUSE_MIN` | 20.0s | Minimum long pause duration |
| `BATCH_PAUSE_MAX` | 40.0s | Maximum long pause duration |
| `BACKOFF_START` | 60s | First backoff wait after a 429/403 |
| `BACKOFF_MAX` | 300s | Maximum backoff cap (5 minutes) |

The browser context is automatically restarted every 200 requests to rotate the fingerprint.

---

## Project Structure

```
.
├── main.py               — CLI entrypoint & orchestration
├── config.py             — Global settings + env abstraction
├── requirements.txt
├── .env.example          — Template for runtime environment variables
├── db/
│   ├── database.py       — Async SQLite manager (aiosqlite)
│   └── schema.sql        — Normalized warehouse schema
├── core/
│   ├── browser.py        — Stealth browser manager
│   ├── rate_limiter.py   — Singleton request throttler
│   ├── checkpoint.py     — Resume state manager
│   └── exporter.py       — CSV export utility
├── scrapers/
│   ├── base.py           — BaseExtractor with shared DOM utilities
│   ├── matches.py
│   ├── match_detail.py
│   ├── match_stats.py
│   ├── players.py
│   ├── player_stats.py
│   ├── teams.py
│   ├── team_stats.py
│   ├── roster_history.py
│   ├── events.py
│   ├── event_detail.py
│   ├── rankings.py
│   ├── player_rankings.py
│   └── news.py
├── checkpoints/          — Auto-created (gitignored)
├── logs/                 — Auto-created (gitignored)
└── exports/              — Auto-created (gitignored)
```

---

## Notes on Responsible Use

- This tool is intended for **personal and research use only**.
- It deliberately uses slow, human-like delays to minimize server load.
- Do not run multiple instances simultaneously.
- Do not use proxies to increase speed — this creates unnecessary load.
- Respect the target platform's Terms of Service and robots.txt.
- All data collected is publicly accessible on the source platform.

---

## License

MIT License — see [LICENSE](LICENSE).
