# `hltv-scraper`

HLTV.org CS2 esports scraper — browser-paced extraction to SQLite.

**Usage**:

```console
$ hltv-scraper [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `export`: Export SQLite tables to CSV and/or JSON...
* `status`: Print row counts for every warehouse table.
* `scrape`: Run one or more domain scrapers.

## `hltv-scraper export`

Export SQLite tables to CSV and/or JSON under exports/.

**Usage**:

```console
$ hltv-scraper export [OPTIONS]
```

**Options**:

* `--csv`: Export all tables to CSV.
* `--json`: Export all tables to JSON.
* `--table TEXT`: Export one table. One of: matches, map_results, player_match_stats, players, player_career_stats, player_event_stats, player_map_stats, teams, team_stats, team_map_stats, roster_history, events, event_teams, world_rankings, player_rankings, news
* `--help`: Show this message and exit.

## `hltv-scraper status`

Print row counts for every warehouse table.

**Usage**:

```console
$ hltv-scraper status [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `hltv-scraper scrape`

Run one or more domain scrapers.

**Usage**:

```console
$ hltv-scraper scrape [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--all`: Run all scrapers in dependency order.
* `--matches`: Matches + map/player match stats.
* `--players`: Players + player stats.
* `--teams`: Teams + team stats + roster history.
* `--events`: Events + event detail placements.
* `--rankings`: World + player ranking snapshots.
* `--news`: News archive.
* `--match-url TEXT`: Scrape one match by full HLTV URL.
* `--help`: Show this message and exit.
