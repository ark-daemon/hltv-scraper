# HLTV Scraper

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-beta-orange.svg)](CHANGELOG.md)

> Async SQLite warehouse for [HLTV.org](https://www.hltv.org) CS2 esports pages -- single-browser crawl, multi-second human pacing, checkpointed extractors, CSV/JSON export, and fleet match snapshots.

**Fleet:** [vlr-scraper](https://github.com/ark-daemon/vlr-scraper) · [dota2-scraper](https://github.com/ark-daemon/dota2-scraper) · [rocket-league-scraper](https://github.com/ark-daemon/rocket-league-scraper) · [lol-esports-scraper](https://github.com/ark-daemon/lol-esports-scraper)

## Features

- **Browser-first crawl** -- CloakBrowser (default) or Camoufox; always rendered HTML
- **Slow by design** -- 4-9s between requests, batch pauses, long 429/403 backoff
- **Checkpoint resume** -- JSON checkpoints + DB primary keys across long backfills
- **Broad domain coverage** -- matches/maps, players, teams, events, rankings, news
- **Table export** -- CSV and/or JSON under `exports/`
- **Fleet snapshot** -- match-grain `export/` (`data.json` + `csv` + `parquet` + `manifest.json`)
- **Optional R2 publish** -- overwrite-in-place upload with public manifest verification

Maturity: **beta (`0.1.0`)**. Built for research backfills where correctness and low request rate matter more than throughput. Not affiliated with HLTV.

## Getting started

```bash
git clone https://github.com/ark-daemon/hltv-scraper.git
cd hltv-scraper

python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate

pip install -e ".[dev]"
# Optional publish extra: pip install -e ".[dev,publish]"
cp .env.example .env

hltv-scraper --help
# equivalent from repo root after install: python main.py --help
```

Default backend is CloakBrowser (Chromium downloads on first launch). Camoufox is an alternate backend declared in dependencies.

## Usage

```bash
hltv-scraper status
hltv-scraper scrape --matches          # results + map/player match stats
hltv-scraper scrape --players
hltv-scraper scrape --teams
hltv-scraper scrape --events
hltv-scraper scrape --rankings
hltv-scraper scrape --news
hltv-scraper scrape --all              # full ordered pipeline (very long)

# One-off match
hltv-scraper scrape --match-url "https://www.hltv.org/matches/<id>/<slug>"

# Table dump (exports/)
hltv-scraper export --csv
hltv-scraper export --json
hltv-scraper export --table matches

# Fleet match snapshot (export/)
hltv-scraper snapshot
hltv-scraper snapshot --publish
hltv-scraper publish
```

Full Typer-generated CLI docs: [COMMANDS.md](COMMANDS.md).

## Architecture

```
CLI (typer: hltv-scraper)
        |
        v
 BrowserManager (CloakBrowser | Camoufox)
        |
        |  rate_limiter.wait() before every fetch
        |  BeautifulSoup(html, "lxml")
        v
 scrapers/*  (one domain per module)
   matches -> match_detail -> match_stats
   players -> player_stats
   teams -> team_stats -> roster_history
   events -> event_detail
   rankings / player_rankings / news
        |
        v
 aiosqlite (hltv.db) + checkpoints/state.json
        |
        +--> exporter -> exports/  (tables)
        +--> snapshot -> export/   (match grain)
```

| Concept | This repo | vlr-scraper |
|---------|-----------|-------------|
| Transport | **Browser-first** (always) | httpx first, browser on CF |
| Circuit breaker | **No** -- 429/403 use long backoff (60s-300s) | Global consecutive-failure breaker |
| Concurrency | Effectively **one request at a time** | Multi-worker queue |
| Resume | Checkpoint JSON + DB PKs | SQLite `crawl_queue` |

Browser context restarts every `BROWSER_RESTART_EVERY` (200) requests.

## Configuration

**Environment / `.env`** (via `python-dotenv` in `config.py`):

| Variable | Default | Role |
|----------|---------|------|
| `DB_PATH` | `hltv.db` | SQLite path |
| `CHECKPOINT_DIR` | `checkpoints/` | Resume state |
| `LOG_DIR` | `logs/` | Log directory |
| `EXPORT_DIR` | `exports/` | Table export directory |
| `BASE_URL` | `https://www.hltv.org` | Origin |
| `BROWSER_BACKEND` | `cloakbrowser` | `cloakbrowser` or `camoufox` |
| `BROWSER_PROXY` | empty | Optional proxy string |
| `BROWSER_GEOIP` | `false` | Camoufox/geo flag when supported |
| `BROWSER_HUMAN_PRESET` | `default` | CloakBrowser human preset |

**Hardcoded in `config.py` (not env-driven):**

| Setting | Value |
|---------|-------|
| `MIN_DELAY` / `MAX_DELAY` | 4.0s - 9.0s between requests |
| `BATCH_PAUSE_EVERY` | 30 requests -> 20-40s pause |
| `MAX_RETRIES` | 10 |
| `BACKOFF_START` / `BACKOFF_MAX` | 60s / 300s after 429/403-class failures |
| `HEADLESS` | `True` |
| `PAGE_LOAD_TIMEOUT` | 30s |
| `BROWSER_RESTART_EVERY` | 200 requests |
| `CHECKPOINT_SAVE_EVERY` | 100 items |

**R2 publish** (optional; needs `boto3` via `pip install -e ".[publish]"`):

| Variable | Role |
|----------|------|
| `R2_ACCOUNT_ID` | Cloudflare account id |
| `R2_ACCESS_KEY_ID` | R2 API token access key |
| `R2_SECRET_ACCESS_KEY` | R2 API token secret |
| `R2_BUCKET` | Bucket name |
| `R2_PUBLIC_BASE_URL` | Public base, no trailing slash |

Objects land at `{base}/hltv/{data.json,data.csv,data.parquet,manifest.json}`.

## Data model

Tables in `db/schema.sql`:
`matches`, `map_results`, `player_match_stats`, `players`, `player_career_stats`, `player_event_stats`, `player_map_stats`, `teams`, `team_stats`, `team_map_stats`, `roster_history`, `events`, `event_teams`, `world_rankings`, `player_rankings`, `news`.

Sample `matches` shape:

```json
{"match_id": 138438, "team1_name": "mTw", "team2_name": "Frankfurt 69ers",
 "team1_score": 2, "team2_score": 1, "event_name": "CB Eurocup XIII",
 "format": "bo3", "status": "completed"}
```

Dated table dumps land under `exports/` (e.g. `matches_YYYY-MM-DD.csv`).

### Fleet snapshot (`export/`)

Match/series grain, `schema_version` **1.0**. Winner is derived from scores when HLTV leaves winner blank (draws stay null). Columns:

`match_id`, `match_date`, `team_a`, `team_b`, `winner`, `source_url`, `status`, `score_a`, `score_b`, `event_name`, `format`, `raw_status`

> [!NOTE]
> `export/` is the **snapshot** bundle. Table dumps go to `exports/` via `export`.

## Limitations

> [!WARNING]
> Full history can take days. Do not run multiple instances against the same site budget. HLTV ToS may prohibit bulk automation; operator owns compliance.

- **Browser-only** -- no lightweight HTTP path
- **Anti-bot friction** -- challenge pages, layout shifts, intermittent empty parses
- **No circuit breaker** -- per-request retry + long sleep, not a global trip
- **Skeleton rows** -- listing scrapers insert stubs; detail scrapers fill stats later
- **Tests** cover parsing helpers, checkpoint atomicity, packaging smoke -- not live HLTV

## Tech stack

| Layer | Used |
|-------|------|
| Runtime | Python >=3.11, asyncio |
| CLI | typer + rich (`hltv-scraper`; `main:app`) |
| Config | python-dotenv + module-level constants |
| Browser | cloakbrowser (default) or camoufox; playwright for stack compatibility |
| HTML | beautifulsoup4 + lxml |
| Storage | aiosqlite |
| Export | pandas `read_sql` -> CSV/JSON; snapshot also writes Parquet |
| Retry helpers | tenacity (dep); primary pacing is custom `RateLimiter` |
| Logging | loguru + rich CLI chrome |
| Publish | boto3 optional (`[publish]`) |
| Quality | pytest, ruff (dev) |
