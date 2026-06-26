"""Tests for database schema — init_db, verify_schema, migration runner."""
import sqlite3
import tempfile
from pathlib import Path

import pytest

from ffootball.db.schema import (
    CORE_TABLES,
    SCHEMA_VERSION,
    init_db,
    verify_schema,
    get_connection,
    _run_migrations,
)


def tmp_db() -> Path:
    """Return a path to a fresh temp DB file."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Path(tmp.name)


def test_init_db_creates_all_tables():
    db = tmp_db()
    init_db(db)
    results = verify_schema(db)
    assert all(results.values()), f"Missing tables: {[k for k, v in results.items() if not v]}"


def test_init_db_idempotent():
    db = tmp_db()
    init_db(db)
    init_db(db)  # second call should not raise
    results = verify_schema(db)
    assert all(results.values())


def test_verify_schema_missing_table():
    db = tmp_db()
    # Create DB without all tables
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.close()
    results = verify_schema(db)
    assert results["config"] is True
    assert results["players"] is False


def test_schema_version_stamped():
    db = tmp_db()
    init_db(db)
    conn = get_connection(db)
    row = conn.execute(
        "SELECT version FROM schema_version WHERE version = ?", (SCHEMA_VERSION,)
    ).fetchone()
    conn.close()
    assert row is not None, "Schema version not stamped"


def test_migration_runner_adds_league_rosters():
    """Simulate a v1 DB missing league_rosters, then run migrations."""
    db = tmp_db()
    # Build a minimal v1 schema (no league_rosters table)
    conn = get_connection(db)
    with conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (1, '2024-01-01')")
        # Add a few tables that v1 would have had
        conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    # Run migrations
    with conn:
        _run_migrations(conn)
    # league_rosters should now exist
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='league_rosters'"
    ).fetchone()
    conn.close()
    assert row is not None, "league_rosters should have been created by migration"


def test_get_connection_returns_row_factory():
    db = tmp_db()
    init_db(db)
    conn = get_connection(db)
    conn.execute("INSERT INTO config (key, value) VALUES ('test_key', 'test_val')")
    row = conn.execute("SELECT key, value FROM config WHERE key='test_key'").fetchone()
    conn.close()
    assert row["key"] == "test_key"
    assert row["value"] == "test_val"
