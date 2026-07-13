"""Smoke tests for packaging and basic imports."""

from pathlib import Path

import config
from db.database import Database


def test_config_defaults():
    assert config.BASE_URL.rstrip("/").endswith("hltv.org")
    assert config.MIN_DELAY > 0


def test_schema_file_present():
    schema = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
    assert schema.exists(), f"Missing schema at {schema}"
    assert "CREATE TABLE" in schema.read_text(encoding="utf-8").upper()


def test_database_class_importable():
    assert Database is not None
