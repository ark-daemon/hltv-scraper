"""
core/checkpoint.py - Resume checkpoint manager.

Stores scraping progress in checkpoints/state.json so any scraper
can be interrupted and resumed without re-scraping already-done items.
"""

import json
import os
from pathlib import Path

from loguru import logger

from config import CHECKPOINT_DIR, CHECKPOINT_SAVE_EVERY

_DEFAULT_STATE = {
    "matches": [],
    "match_stats": [],
    "players": [],
    "player_stats": [],
    "teams": [],
    "team_stats": [],
    "roster_history": [],
    "events": [],
    "event_detail": [],
    "rankings": [],
    "player_rankings": [],
    "news": [],
}


class Checkpoint:
    """
    Manages scraper progress via a JSON state file.

    Every scraper must:
      1. Call is_done(scraper_name, item_id) before processing
      2. Call mark_done(scraper_name, item_id) after saving to DB
      3. Call save() every CHECKPOINT_SAVE_EVERY items
    """

    def __init__(self) -> None:
        self._path = Path(CHECKPOINT_DIR) / "state.json"
        self._sets: dict[str, set] = {}
        self._dirty_count: int = 0

    def load(self) -> None:
        """Load state from state.json. Creates empty state if not found."""
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        tmp_path = str(self._path) + ".tmp"
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    raw_state = json.load(f)

                for key, default in _DEFAULT_STATE.items():
                    if key not in raw_state:
                        raw_state[key] = default

                self._sets = {k: set(v) for k, v in raw_state.items()}
                total = sum(len(v) for v in raw_state.values())
                logger.info(
                    f"[Checkpoint] Loaded state.json - {total} completed items across all scrapers."
                )
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(
                    f"[Checkpoint] Could not load state.json ({e}). Starting fresh."
                )
                self._init_empty()
        else:
            logger.info("[Checkpoint] No state.json found. Starting fresh.")
            self._init_empty()

    def _init_empty(self) -> None:
        self._sets = {k: set() for k in _DEFAULT_STATE}

    def save(self) -> None:
        """Write current state to state.json atomically via a temp file."""
        tmp_path = str(self._path) + ".tmp"
        try:
            os.makedirs(CHECKPOINT_DIR, exist_ok=True)
            serializable = {k: list(v) for k, v in self._sets.items()}
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(serializable, f, indent=2)
            os.replace(tmp_path, self._path)
            self._dirty_count = 0
            logger.debug("[Checkpoint] State saved.")
        except Exception as e:
            logger.error(f"[Checkpoint] Failed to save state: {e}")

    def is_done(self, scraper_name: str, item_id) -> bool:
        """
        Returns True if this item has already been processed by this scraper.
        item_id is stored as a string for JSON compatibility.
        """
        key = str(item_id)
        return key in self._sets.get(scraper_name, set())

    def mark_done(
        self, scraper_name: str, item_id, auto_save_every: int = CHECKPOINT_SAVE_EVERY
    ) -> None:
        """
        Mark an item as done. Auto-saves every auto_save_every marks.
        """
        key = str(item_id)
        if scraper_name not in self._sets:
            self._sets[scraper_name] = set()
        self._sets[scraper_name].add(key)
        self._dirty_count += 1
        if self._dirty_count >= auto_save_every:
            self.save()

    def get_done_set(self, scraper_name: str) -> set:
        """Return the full set of completed item IDs for a scraper."""
        return self._sets.get(scraper_name, set())

    def count(self, scraper_name: str) -> int:
        """Return how many items are done for a scraper."""
        return len(self._sets.get(scraper_name, set()))
