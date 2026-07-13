# Contributing

Thanks for interest in **hltv-scraper** (CS2 esports data from HLTV.org).

## Ground rules

- Keep rate limits conservative; do not add default proxy rotation or concurrent flood options.
- Prefer defensive parsers (missing fields → `NULL`) over hard failures.
- Do not commit scraped databases, exports, logs, cookies, or real `.env` files.
- Be honest in docs: name the target sites; do not market anti-bot bypass as a feature.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
```

## Pull requests

1. Fork and branch from `main`.
2. Add or update tests for parser/storage changes.
3. Run `pytest -q` (and ruff if installed).
4. Keep PRs focused; explain *why* the change is needed.

## Bug reports

Include: Python version, OS, command run, relevant log snippet, and whether the issue is install, fetch, or parse related.

## License

By contributing, you agree your contributions are licensed under the MIT License.
