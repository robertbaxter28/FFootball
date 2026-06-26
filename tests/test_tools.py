"""Tests for agent tool handlers."""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ffootball.db.schema import init_db, get_connection
from ffootball.db.queries import set_config_value
from ffootball.agent.tools import ToolHandler


def _make_cfg(db_path: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.db_path = db_path
    cfg.context_dir = db_path.parent / "context"
    cfg.context_dir.mkdir(exist_ok=True)
    cfg.sleeper_season = "2025"
    cfg.sleeper_league_id = "test_league"
    return cfg


def _tmp_db() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = Path(tmp.name)
    init_db(db)
    return db


class TestQueryDatabase:
    def test_select_returns_rows(self):
        db = _tmp_db()
        cfg = _make_cfg(db)
        conn = get_connection(db)
        with conn:
            set_config_value(conn, "test_key", "test_value")
        conn.close()
        handler = ToolHandler(cfg)
        result = json.loads(handler.dispatch("query_database", {"sql": "SELECT key, value FROM config"}))
        # query_database returns {"columns": [...], "rows": [...], "count": N}
        assert "rows" in result
        assert "columns" in result
        assert any(r["key"] == "test_key" for r in result["rows"])

    def test_select_blocks_insert(self):
        db = _tmp_db()
        cfg = _make_cfg(db)
        handler = ToolHandler(cfg)
        result = json.loads(handler.dispatch("query_database", {
            "sql": "INSERT INTO config (key, value) VALUES ('x', 'y')"
        }))
        assert "error" in result

    def test_select_blocks_delete(self):
        db = _tmp_db()
        cfg = _make_cfg(db)
        handler = ToolHandler(cfg)
        result = json.loads(handler.dispatch("query_database", {
            "sql": "DELETE FROM config"
        }))
        assert "error" in result

    def test_invalid_sql_returns_error(self):
        db = _tmp_db()
        cfg = _make_cfg(db)
        handler = ToolHandler(cfg)
        result = json.loads(handler.dispatch("query_database", {"sql": "SELECT * FROM nonexistent_table"}))
        assert "error" in result

    def test_auto_limit_applied(self):
        db = _tmp_db()
        cfg = _make_cfg(db)
        handler = ToolHandler(cfg)
        # Query without LIMIT — should not raise
        result = handler.dispatch("query_database", {"sql": "SELECT * FROM config"})
        assert result is not None


class TestJournalTools:
    def test_add_journal_entry(self):
        db = _tmp_db()
        cfg = _make_cfg(db)
        handler = ToolHandler(cfg)
        result = json.loads(handler.dispatch("add_journal_entry", {
            "decision_type": "trade",
            "title": "Traded Kelce for 1st",
            "decision_made": "Accepted trade",
            "reasoning": "Win-now move",
            "season": "2025",
        }))
        assert result.get("created") is True
        assert "id" in result

    def test_grade_journal_entry(self):
        db = _tmp_db()
        cfg = _make_cfg(db)
        handler = ToolHandler(cfg)
        add = json.loads(handler.dispatch("add_journal_entry", {
            "decision_type": "waiver",
            "title": "Picked up rookie RB",
            "decision_made": "Added via waiver",
            "season": "2025",
        }))
        entry_id = add["id"]
        grade_result = json.loads(handler.dispatch("grade_journal_entry", {
            "id": entry_id,
            "grade": "A",
            "outcome_notes": "Great pickup — won championship",
        }))
        assert grade_result.get("graded") is True

    def test_grade_nonexistent_entry(self):
        db = _tmp_db()
        cfg = _make_cfg(db)
        handler = ToolHandler(cfg)
        result = json.loads(handler.dispatch("grade_journal_entry", {
            "id": 9999,
            "grade": "B",
            "outcome_notes": "N/A",
        }))
        assert "error" in result

    def test_search_journal(self):
        db = _tmp_db()
        cfg = _make_cfg(db)
        handler = ToolHandler(cfg)
        # Add two entries
        handler.dispatch("add_journal_entry", {
            "decision_type": "trade",
            "title": "Sold aging RB",
            "decision_made": "Traded away old RB for picks",
            "reasoning": "Dynasty rebuild",
            "season": "2025",
        })
        handler.dispatch("add_journal_entry", {
            "decision_type": "waiver",
            "title": "Dropped backup TE",
            "decision_made": "Released him",
            "season": "2025",
        })
        result = json.loads(handler.dispatch("search_journal", {
            "keywords": "aging",  # substring present in "Sold aging RB"
        }))
        # search_journal returns {"count": N, "entries": [...]}
        assert "entries" in result
        assert result["count"] >= 1
        assert any("Sold aging RB" in e["title"] for e in result["entries"])


class TestUpdatePlayerNote:
    def test_creates_note(self):
        db = _tmp_db()
        cfg = _make_cfg(db)
        # Seed a player first
        conn = get_connection(db)
        conn.execute(
            "INSERT INTO players (player_id, full_name, position, synced_at) VALUES (?,?,?,?)",
            ("p99", "Test Player", "WR", "2025-01-01T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()

        handler = ToolHandler(cfg)
        result = json.loads(handler.dispatch("update_player_note", {
            "player_id": "p99",
            "dynasty_grade": "A",
            "action_flag": "buy",
            "notes": "Great route runner",
        }))
        assert result.get("updated") is True

    def test_creates_note_without_player_in_db(self):
        db = _tmp_db()
        cfg = _make_cfg(db)
        handler = ToolHandler(cfg)
        result = json.loads(handler.dispatch("update_player_note", {
            "player_id": "unknown_player",
            "dynasty_grade": "B",
        }))
        # Should succeed since player_notes has no FK constraint
        assert result.get("updated") is True


class TestGetLeagueSettings:
    def test_returns_error_when_empty(self):
        db = _tmp_db()
        cfg = _make_cfg(db)
        handler = ToolHandler(cfg)
        result = json.loads(handler.dispatch("get_league_settings", {}))
        assert "error" in result

    def test_returns_settings_when_populated(self):
        db = _tmp_db()
        cfg = _make_cfg(db)
        conn = get_connection(db)
        import json as _json
        conn.execute(
            """
            INSERT INTO league_settings
              (league_id, season, name, total_rosters, roster_positions,
               scoring_settings, taxi_slots, ir_slots, trade_deadline,
               playoff_week_start, playoff_teams, waiver_type, waiver_budget,
               raw_json, synced_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("lg1", "2025", "Dynasty Test", 12,
             _json.dumps(["QB", "RB", "WR", "TE"]),
             _json.dumps({"rec": 1.0}),
             2, 1, 11, 15, 6, 2, 100, _json.dumps({}), "2025-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()
        handler = ToolHandler(cfg)
        result = json.loads(handler.dispatch("get_league_settings", {}))
        assert result["name"] == "Dynasty Test"
        assert result["total_rosters"] == 12
