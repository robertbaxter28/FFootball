"""Tests for in-agent journal CLI functions."""
import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ffootball.db.schema import init_db, get_connection
from ffootball.agent.agent import _journal_list, _journal_stats, _journal_view
from ffootball.agent.tools import ToolHandler


def _make_cfg(db_path: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.db_path = db_path
    cfg.context_dir = db_path.parent / "context"
    cfg.context_dir.mkdir(exist_ok=True)
    cfg.sleeper_season = "2025"
    return cfg


def _tmp_db() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = Path(tmp.name)
    init_db(db)
    return db


def _seed_entries(db: Path) -> list[int]:
    conn = get_connection(db)
    ids = []
    entries = [
        ("trade", "Sold Kelce", "Traded Kelce for picks", "Dynasty rebuild", "A", "2025"),
        ("waiver", "Picked up rookie", "Added via waiver", None, None, "2025"),
        ("drop", "Cut aging RB", "Released him", "Roster crunch", "B", "2024"),
    ]
    for dtype, title, decision, reasoning, grade, season in entries:
        cur = conn.execute(
            """INSERT INTO decision_journal
               (created_at, decision_type, title, decision_made, reasoning, season, outcome_grade)
               VALUES (datetime('now'), ?,?,?,?,?,?)""",
            (dtype, title, decision, reasoning, season, grade),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return ids


class TestJournalList:
    def test_shows_entries(self, capsys):
        db = _tmp_db()
        _seed_entries(db)
        cfg = _make_cfg(db)
        _journal_list(cfg)
        out = capsys.readouterr().out
        assert "Sold Kelce" in out
        assert "Picked up rookie" in out

    def test_filter_by_type(self, capsys):
        db = _tmp_db()
        _seed_entries(db)
        cfg = _make_cfg(db)
        _journal_list(cfg, type_filter="trade")
        out = capsys.readouterr().out
        assert "Sold Kelce" in out
        assert "Picked up rookie" not in out

    def test_filter_by_season(self, capsys):
        db = _tmp_db()
        _seed_entries(db)
        cfg = _make_cfg(db)
        _journal_list(cfg, season_filter="2024")
        out = capsys.readouterr().out
        assert "Cut aging RB" in out
        assert "Sold Kelce" not in out

    def test_empty_db_no_error(self, capsys):
        db = _tmp_db()
        cfg = _make_cfg(db)
        _journal_list(cfg)
        out = capsys.readouterr().out
        assert "No journal entries" in out


class TestJournalStats:
    def test_shows_grade_breakdown(self, capsys):
        db = _tmp_db()
        _seed_entries(db)
        cfg = _make_cfg(db)
        _journal_stats(cfg)
        out = capsys.readouterr().out
        assert "Grade breakdown" in out
        assert "Total entries" in out

    def test_win_rate_calculated(self, capsys):
        db = _tmp_db()
        _seed_entries(db)
        cfg = _make_cfg(db)
        _journal_stats(cfg)
        out = capsys.readouterr().out
        assert "Win rate" in out

    def test_empty_db_no_error(self, capsys):
        db = _tmp_db()
        cfg = _make_cfg(db)
        _journal_stats(cfg)
        out = capsys.readouterr().out
        assert "No journal entries" in out


class TestJournalView:
    def test_shows_entry_detail(self, capsys):
        db = _tmp_db()
        ids = _seed_entries(db)
        cfg = _make_cfg(db)
        _journal_view(cfg, ids[0])
        out = capsys.readouterr().out
        assert "Sold Kelce" in out
        assert "Dynasty rebuild" in out

    def test_missing_id_graceful(self, capsys):
        db = _tmp_db()
        cfg = _make_cfg(db)
        _journal_view(cfg, 9999)
        out = capsys.readouterr().out
        assert "9999" in out
