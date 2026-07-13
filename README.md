# hltv-scraper

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-beta-orange.svg)](CHANGELOG.md)

Async SQLite warehouse for **CS2 esports pages on [HLTV.org](https://www.hltv.org)** — single-browser crawl, multi-second human pacing, checkpointed extractors, CSV/JSON export.

**Fleet:** [vlr-scraper](https://github.com/ark-daemon/vlr-scraper) · [dota2-scraper](https://github.com/ark-daemon/dota2-scraper) · [rocket-league-scraper](https://github.com/ark-daemon/rocket-league-scraper) · [lol-esports-scraper](https://github.com/ark-daemon/lol-esports-scraper)

---

## What it does

Scrapes publicly visible HLTV pages into a local warehouse: results, map scoreboards, player match/career/event/map stats, teams, roster history, events, weekly world rankings, player rankings, and news. Built for research backfills where correctness and low request rate matter more than throughput.

Maturity: **beta (`0.1.0`)**. Default pacing is deliberately slow (seconds per page). Not affiliated with HLTV.

---

## Architecture

```
CLI (argparse) â”€â”€â–º BrowserManager (CloakBrowser | Camoufox)
                          │
                          │  rate_limiter.wait() before every fetch
                          │  BeautifulSoup(html, "lxml")
                          â–¼
              scrapers/* (one domain per module)
                matches → match_detail → match_stats
                players → player_stats
                teams → team_stats → roster_history
                events → event_detail
                rankings / player_rankings / news
                          │
                          â–¼
              aiosqlite (hltv.db) + checkpoints/state.json
                          │
                          â–¼
              exporter → CSV and/or JSON under exports/
```

**Important differences from vlr-scraper:**

| Concept | This repo | vlr-scraper |
|---------|-----------|-------------|
| Transport | **Browser-first** (always) | httpx first, browser on CF |
| Circuit breaker | **No** — 429/403 use long backoff (60s→300s) | Global consecutive-failure breaker |
| Concurrency | Effectively **one request at a time** via shared rate limiter | Multi-worker queue |
| Resume | Checkpoint JSON + DB primary keys | SQLite `crawl_queue` |

Browser context restarts every `BROWSER_RESTART_EVERY` (200) requests.

---

## Quickstart

```bash
git clone https://github.com/ark-daemon/hltv-scraper.git
cd hltv-scraper

python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate

pip install -e ".[dev]"
# Default backend is CloakBrowser (Chromium downloaded on first launch).
# Optional alternate backend: Camoufox (also declared in dependencies).

cp .env.example .env

hltv-scraper --help
# equivalent from repo root after install:
# python main.py --help

hltv-scraper status
hltv-scraper scrape --matches          # results + map/player match stats
hltv-scraper scrape --players
hltv-scraper scrape --teams
hltv-scraper scrape --events
hltv-scraper scrape --rankings
hltv-scraper scrape --news
hltv-scraper scrape --all              # full ordered pipeline (very long)

hltv-scraper export --csv
hltv-scraper export --json
hltv-scraper export --table matches
```

One-off match:

```bash
hltv-scraper scrape --match-url "https://www.hltv.org/matches/<id>/<slug>"
```

---

## Configuration

**Environment / `.env`** (via `python-dotenv` in `config.py`):

| Variable | Default | Role |
|----------|---------|------|
| `DB_PATH` | `hltv.db` | SQLite path |
| `CHECKPOINT_DIR` | `checkpoints/` | Resume state |
| `LOG_DIR` | `logs/` | Log directory |
| `EXPORT_DIR` | `exports/` | Export directory |
| `BASE_URL` | `https://www.hltv.org` | Origin |
| `BROWSER_BACKEND` | `cloakbrowser` | `cloakbrowser` or `camoufox` |
| `BROWSER_PROXY` | empty | Optional proxy string |
| `BROWSER_GEOIP` | `false` | Camoufox/geo flag when supported |
| `BROWSER_HUMAN_PRESET` | `default` | CloakBrowser human preset |

**Hardcoded in `config.py` (not env-driven):**

| Setting | Value |
|---------|-------|
| `MIN_DELAY` / `MAX_DELAY` | 4.0s “ 9.0s between requests |
| `BATCH_PAUSE_EVERY` | 30 requests → 20“40s pause |
| `MAX_RETRIES` | 10 |
| `BACKOFF_START` / `BACKOFF_MAX` | 60s / 300s after 429/403-class failures |
| `HEADLESS` | `True` |
| `PAGE_LOAD_TIMEOUT` | 30s |
| `BROWSER_RESTART_EVERY` | 200 requests |
| `CHECKPOINT_SAVE_EVERY` | 100 items |

---

## Data model + sample output

Tables in `db/schema.sql`:  
`matches`, `map_results`, `player_match_stats`, `players`, `player_career_stats`, `player_event_stats`, `player_map_stats`, `teams`, `team_stats`, `team_map_stats`, `roster_history`, `events`, `event_teams`, `world_rankings`, `player_rankings`, `news`.

**Sample `matches` rows** (from a real local DB after scrape):

```json
{"match_id": 138438, "team1_name": "mTw", "team2_name": "Frankfurt 69ers",
 "team1_score": 2, "team2_score": 1, "event_name": "CB Eurocup XIII",
 "format": "bo3", "status": "completed"}

{"match_id": 139513, "team1_name": "mousesports", "team2_name": "Pentagram",
 "team1_score": 2, "team2_score": 16, "event_name": "NGL-One",
 "format": "inf", "status": "completed"}
```

Exports land as dated files under `exports/` (e.g. `matches_YYYY-MM-DD.csv`).

---

## Current limitations

- **Slow by design.** Full history can take days; do not run multiple instances.
- **Browser-only.** No lightweight HTTP path; CloakBrowser/Camoufox required.
- **Anti-bot friction.** Challenge pages, layout shifts, and intermittent empty parses are expected; extractors log and continue.
- **Not a circuit breaker.** Unlike vlr-scraper, failures use per-request retry + long sleep, not a global trip.
- **Data gaps.** Listing scrapers may insert skeleton rows; detail scrapers fill stats later — intermediate DBs look sparse.
- **Legal/ToS risk.** HLTV ToS may prohibit bulk automation; operator owns compliance.
- **Tests** cover parsing helpers, checkpoint atomicity, and packaging smoke — not live HLTV pages.

---

## Tech stack

| Layer | Actually used |
|-------|----------------|
| Runtime | Python â‰¥3.11, asyncio |
| CLI | argparse (`main.py` / `hltv-scraper` entry) |
| Config | python-dotenv + module-level constants |
| Browser | **cloakbrowser** (default) or **camoufox**; playwright listed for stack compatibility |
| HTML | beautifulsoup4 + **lxml** parser |
| Storage | aiosqlite |
| Export | pandas `read_sql` → CSV/JSON |
| Retry helpers | tenacity (dependency); primary pacing is custom `RateLimiter` |
| Logging | loguru |
| Quality | pytest (dev) |

---

## License

MIT © ark-daemon — see [LICENSE](LICENSE).

See also [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [CHANGELOG.md](CHANGELOG.md).

## Command reference

Full Typer-generated CLI docs: [COMMANDS.md](COMMANDS.md).
