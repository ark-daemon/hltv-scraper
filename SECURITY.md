# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.0.x | Yes |

## Reporting a vulnerability

If you discover a security issue in **hltv-scraper** (for example leaked credentials in logs, unsafe deserialization, or path traversal):

1. **Do not** open a public GitHub issue.
2. Email or contact the maintainer via GitHub: [@ark-daemon](https://github.com/ark-daemon).
3. Include steps to reproduce, impact, and any suggested fix.

We will acknowledge reports as soon as practical and coordinate disclosure.

## Operational security notes

- Never commit .env, browser session cookies, or SQLite databases with production data.
- Use a descriptive `User-Agent` with a real contact address.
- Scraping third-party sites may violate their Terms of Service; that is a user responsibility.
