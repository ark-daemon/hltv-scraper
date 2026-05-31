-- Data Extraction Pipeline Database Schema

CREATE TABLE IF NOT EXISTS matches (
    match_id        INTEGER PRIMARY KEY,
    date            TEXT,
    timestamp       INTEGER,
    team1_id        INTEGER,
    team2_id        INTEGER,
    team1_name      TEXT,
    team2_name      TEXT,
    team1_score     INTEGER,
    team2_score     INTEGER,
    winner_id       INTEGER,
    format          TEXT,
    event_id        INTEGER,
    event_name      TEXT,
    lan             INTEGER,
    stars           INTEGER,
    status          TEXT,
    hltv_url        TEXT,
    scraped_at      TEXT
);

CREATE TABLE IF NOT EXISTS map_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        INTEGER,
    map_number      INTEGER,
    map_name        TEXT,
    team1_score     INTEGER,
    team2_score     INTEGER,
    team1_ct_score  INTEGER,
    team1_t_score   INTEGER,
    team2_ct_score  INTEGER,
    team2_t_score   INTEGER,
    winner_id       INTEGER,
    FOREIGN KEY (match_id) REFERENCES matches(match_id),
    UNIQUE (match_id, map_number)
);

CREATE TABLE IF NOT EXISTS player_match_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        INTEGER,
    map_name        TEXT,
    map_number      INTEGER,
    player_id       INTEGER,
    player_name     TEXT,
    team_id         INTEGER,
    team_name       TEXT,
    kills           INTEGER,
    deaths          INTEGER,
    assists         INTEGER,
    rating_2        REAL,
    adr             REAL,
    kast            REAL,
    hs_kills        INTEGER,
    hs_percent      REAL,
    flash_assists   INTEGER,
    opening_kills   INTEGER,
    opening_deaths  INTEGER,
    opening_ratio   REAL,
    kd_diff         INTEGER,
    kd_ratio        REAL,
    first_kills_ct  INTEGER,
    first_kills_t   INTEGER,
    first_deaths_ct INTEGER,
    first_deaths_t  INTEGER,
    k1              INTEGER,
    k2              INTEGER,
    k3              INTEGER,
    k4              INTEGER,
    k5              INTEGER,
    side            TEXT,
    FOREIGN KEY (match_id) REFERENCES matches(match_id),
    UNIQUE (match_id, map_number, player_id, side)
);

CREATE TABLE IF NOT EXISTS players (
    player_id       INTEGER PRIMARY KEY,
    nickname        TEXT,
    real_name       TEXT,
    country         TEXT,
    country_code    TEXT,
    age             INTEGER,
    birth_date      TEXT,
    team_id         INTEGER,
    team_name       TEXT,
    role            TEXT,
    twitter         TEXT,
    twitch          TEXT,
    hltv_url        TEXT,
    photo_url       TEXT,
    is_retired      INTEGER,
    scraped_at      TEXT
);

CREATE TABLE IF NOT EXISTS player_career_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       INTEGER,
    maps_played     INTEGER,
    rounds_played   INTEGER,
    kills           INTEGER,
    deaths          INTEGER,
    assists         INTEGER,
    rating_2        REAL,
    kd_ratio        REAL,
    kd_diff         INTEGER,
    hs_percent      REAL,
    headshots       INTEGER,
    kast            REAL,
    adr             REAL,
    impact          REAL,
    dpr             REAL,
    spr             REAL,
    opening_kills   INTEGER,
    opening_deaths  INTEGER,
    opening_ratio   REAL,
    opening_rating  REAL,
    rifle_kills     INTEGER,
    sniper_kills    INTEGER,
    smg_kills       INTEGER,
    pistol_kills    INTEGER,
    grenade_kills   INTEGER,
    mvp_stars       INTEGER,
    period_from     TEXT,
    period_to       TEXT,
    FOREIGN KEY (player_id) REFERENCES players(player_id),
    UNIQUE (player_id, period_from, period_to)
);

CREATE TABLE IF NOT EXISTS player_event_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       INTEGER,
    event_id        INTEGER,
    event_name      TEXT,
    maps_played     INTEGER,
    rounds_played   INTEGER,
    rating_2        REAL,
    kd_ratio        REAL,
    kd_diff         INTEGER,
    hs_percent      REAL,
    kast            REAL,
    adr             REAL,
    kills           INTEGER,
    deaths          INTEGER,
    assists         INTEGER,
    FOREIGN KEY (player_id) REFERENCES players(player_id),
    UNIQUE (player_id, event_id)
);

CREATE TABLE IF NOT EXISTS player_map_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       INTEGER,
    map_name        TEXT,
    maps_played     INTEGER,
    rating_2        REAL,
    kd_ratio        REAL,
    hs_percent      REAL,
    kast            REAL,
    adr             REAL,
    kills           INTEGER,
    deaths          INTEGER,
    FOREIGN KEY (player_id) REFERENCES players(player_id),
    UNIQUE (player_id, map_name)
);

CREATE TABLE IF NOT EXISTS teams (
    team_id         INTEGER PRIMARY KEY,
    name            TEXT,
    country         TEXT,
    country_code    TEXT,
    logo_url        TEXT,
    hltv_url        TEXT,
    world_ranking   INTEGER,
    hltv_points     INTEGER,
    weeks_in_top30  INTEGER,
    avg_player_age  REAL,
    coach           TEXT,
    coach_id        INTEGER,
    is_active       INTEGER,
    scraped_at      TEXT
);

CREATE TABLE IF NOT EXISTS team_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id         INTEGER,
    maps_played     INTEGER,
    wins            INTEGER,
    losses          INTEGER,
    draws           INTEGER,
    win_rate        REAL,
    rounds_played   INTEGER,
    rounds_won      INTEGER,
    rounds_lost     INTEGER,
    kd_ratio        REAL,
    rating          REAL,
    period_from     TEXT,
    period_to       TEXT,
    FOREIGN KEY (team_id) REFERENCES teams(team_id),
    UNIQUE (team_id, period_from, period_to)
);

CREATE TABLE IF NOT EXISTS team_map_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id         INTEGER,
    map_name        TEXT,
    maps_played     INTEGER,
    wins            INTEGER,
    losses          INTEGER,
    win_rate        REAL,
    ct_rounds_won   INTEGER,
    t_rounds_won    INTEGER,
    FOREIGN KEY (team_id) REFERENCES teams(team_id),
    UNIQUE (team_id, map_name)
);

CREATE TABLE IF NOT EXISTS roster_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id         INTEGER,
    player_id       INTEGER,
    player_name     TEXT,
    date_joined     TEXT,
    date_left       TEXT,
    is_active       INTEGER,
    is_coach        INTEGER,
    FOREIGN KEY (team_id) REFERENCES teams(team_id),
    UNIQUE (team_id, player_id, date_joined, date_left, is_coach)
);

CREATE TABLE IF NOT EXISTS events (
    event_id        INTEGER PRIMARY KEY,
    name            TEXT,
    date_start      TEXT,
    date_end        TEXT,
    location        TEXT,
    country         TEXT,
    prize_pool      TEXT,
    prize_pool_usd  INTEGER,
    num_teams       INTEGER,
    event_type      TEXT,
    tier            TEXT,
    hltv_url        TEXT,
    logo_url        TEXT,
    is_completed    INTEGER,
    scraped_at      TEXT
);

CREATE TABLE IF NOT EXISTS event_teams (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        INTEGER,
    team_id         INTEGER,
    team_name       TEXT,
    placement       INTEGER,
    prize           TEXT,
    prize_usd       INTEGER,
    qualified_via   TEXT,
    FOREIGN KEY (event_id) REFERENCES events(event_id),
    UNIQUE (event_id, team_id)
);

CREATE TABLE IF NOT EXISTS world_rankings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT,
    rank            INTEGER,
    team_id         INTEGER,
    team_name       TEXT,
    points          INTEGER,
    rank_change     INTEGER,
    UNIQUE (snapshot_date, rank)
);

CREATE TABLE IF NOT EXISTS player_rankings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    year            INTEGER,
    rank            INTEGER,
    player_id       INTEGER,
    player_name     TEXT,
    team_name       TEXT,
    rating          REAL,
    UNIQUE (year, rank)
);

CREATE TABLE IF NOT EXISTS news (
    news_id         INTEGER PRIMARY KEY,
    title           TEXT,
    date            TEXT,
    author          TEXT,
    category        TEXT,
    hltv_url        TEXT,
    related_team_id INTEGER,
    related_player_id INTEGER,
    related_event_id INTEGER,
    scraped_at      TEXT
);
