# Changelog

All notable changes to **hltv-scraper** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Planned
- Broader golden HTML/JSON fixtures for site layout regressions
- Schema reference documentation for consumers of exported tables

## [0.1.0] - 2026-07-13

### Added
- Installable package layout and console CLI entry point
- GitHub Actions CI (install + pytest + CLI smoke)
- `SECURITY.md`, `CONTRIBUTING.md`, and this changelog
- Smoke tests for settings, schema packaging, and imports

### Changed
- README rewritten for clear CS2 / source identification (HLTV.org)
- Packaging metadata, license copyright, and public defaults hardened for open-source use

### Fixed
- Install/path issues that broke `pip install` outside the repository root
