# HLTV Scraper — CS2 Esports Data Pipeline

Async Python 3.11+ scraper for **Counter-Strike 2 esports data from [HLTV.org](https://www.hltv.org)** — matches, map stats, players, teams, events, world rankings, and news.

Data is stored in a local SQLite warehouse with checkpoint/resume support and CSV/JSON export.

---

## Install

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -e ".[dev]"
# Browser backends (pick one):
# CloakBrowser downloads its Chromium on first use.
# Or: playwright install chromium
```

```bash
cp .env.example .env
```

## Usage

```bash
hltv-scraper scrape --all
python main.py scrape --all   # equivalent when run from repo root
hltv-scraper status
hltv-scraper export --csv
```

Individual extractors:

```bash
hltv-scraper scrape --matches
hltv-scraper scrape --players
hltv-scraper scrape --teams
hltv-scraper scrape --events
hltv-scraper scrape --rankings
hltv-scraper scrape --news
```

## Rate limiting

Defaults are intentionally slow (single browser session, multi-second delays) to reduce load on HLTV:

| Setting | Default |
|---------|---------|
| `MIN_DELAY` / `MAX_DELAY` | 4s – 9s between requests |
| Batch pause every 30 requests | 20s – 40s |
| Backoff on 429/403 | 60s – 300s |

Do not run multiple instances at once.

## Testing

```bash
pytest -q
```

## Responsible use

- For personal research and analytics only.
- Respect HLTV Terms of Service and robots.txt.
- This project is **not** affiliated with HLTV.org.
- Stealth browser tooling is used only to keep long archival runs stable; do not use it to overwhelm the site.

## License

MIT © 2026 ark-daemon — see [LICENSE](LICENSE).
