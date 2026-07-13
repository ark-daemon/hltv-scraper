"""
config.py - Global configuration for the data extraction pipeline.
"""

import os
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "hltv.db")
CHECKPOINT_DIR = os.getenv("CHECKPOINT_DIR", "checkpoints/")
LOG_DIR = os.getenv("LOG_DIR", "logs/")
EXPORT_DIR = os.getenv("EXPORT_DIR", "exports/")
BASE_URL = os.getenv("BASE_URL", "https://www.hltv.org").rstrip("/")

# --- Delay settings (NO proxies, human-like pacing) ---
MIN_DELAY = 4.0  # minimum seconds between every request
MAX_DELAY = 9.0  # maximum seconds between every request
BATCH_PAUSE_EVERY = 30  # after every 30 requests, take a longer pause
BATCH_PAUSE_MIN = 20.0  # minimum seconds for the longer pause
BATCH_PAUSE_MAX = 40.0  # maximum seconds for the longer pause

# --- Retry / backoff settings ---
MAX_RETRIES = 10
BACKOFF_START = 60  # seconds to wait after first 429/403
BACKOFF_MAX = 300  # cap backoff at 5 minutes

# --- Browser settings ---
BROWSER_BACKEND = os.getenv("BROWSER_BACKEND", "cloakbrowser")
HEADLESS = True
PAGE_LOAD_TIMEOUT = 30  # seconds to wait for page to fully load
BROWSER_RETRY_WAIT = 5  # seconds to wait before retrying a failed page
BROWSER_RESTART_EVERY = (
    200  # restart browser session every N requests to rotate fingerprint
)
BROWSER_PROXY = os.getenv("BROWSER_PROXY") or None
BROWSER_GEOIP = os.getenv("BROWSER_GEOIP", "false").lower() == "true"
BROWSER_HUMAN_PRESET = os.getenv("BROWSER_HUMAN_PRESET", "default")

# --- Checkpoint ---
CHECKPOINT_SAVE_EVERY = 100  # save checkpoint every N items processed
