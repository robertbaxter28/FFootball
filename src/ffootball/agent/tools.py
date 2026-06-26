"""
Claude tool definitions and handlers for the dynasty agent.

All tools are read-only except the journal/note write tools.
query_database is intentionally general — the agent constructs SQL as needed.
"""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from ffootball.config import Config
from ffootball.db.schema import get_connection

# ---------------------------------------------------------------------------
# Tool schemas (sent to Claude API)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "query_database",
        "description": (
            "Execute a read-only SQL query against the local dynasty.db database. "
            "Use this to look up roster, player info, draft picks, matchups, transactions, "
            "journal entries, player notes, and league settings. "
            "Always SELECT — no INSERT/UPDATE/DELETE. "
            "Key tables: players, roster, draft_picks, matchups, transactions, "
            "decision_journal, player_notes, league_settings, config."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "A SELECT SQL statement. Parameterized queries not supported — use literal values.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Optional LIMIT to add if not already in the query (default: 100).",
                    "default": 100,
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "sync_data",
        "description": (
            "Trigger a sync to pull fresh data from the Sleeper API. "
            "Scopes: 'full' (all data), 'league' (settings), 'players' (bulk player universe), "
            "'roster' (current roster + picks), 'matchups' (current week), "
            "'transactions' (last 4 weeks)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["full", "league", "players", "roster", "matchups", "transactions"],
                    "description": "What to sync.",
                }
            },
            "required": ["scope"],
        },
    },
    {
        "name": "read_context_file",
        "description": (
            "Read a markdown context file. "
            "Available files: 'roster_overview' (current roster with grades), "
            "'season_context' (current week, matchups, recent transactions, injuries), "
            "'league_strategy' (your personal strategy notes — hand-maintained)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "enum": ["roster_overview", "season_context", "league_strategy"],
                    "description": "Which context file to read (without .md extension).",
                }
            },
            "required": ["filename"],
        },
    },
    {
        "name": "add_journal_entry",
        "description": (
            "Record a decision to the decision journal. Call this when the user makes "
            "or is considering a significant decision (trade, cut, waiver claim, lineup). "
            "Capturing the reasoning now enables retroactive grading and learning."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "decision_type": {
                    "type": "string",
                    "enum": ["trade", "waiver", "drop", "lineup", "cut", "other"],
                },
                "title": {"type": "string", "description": "Short description, e.g. 'Traded WR Adams for RB Henry'"},
                "context": {"type": "string", "description": "What situation prompted this decision"},
                "options_considered": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Other options that were considered",
                },
                "decision_made": {"type": "string", "description": "What was actually decided/done"},
                "reasoning": {"type": "string", "description": "Why this decision was made"},
                "assets_involved": {
                    "type": "object",
                    "description": "Players/picks involved. E.g. {in: ['player_id'], out: ['player_id']}",
                },
                "week": {"type": "integer", "description": "NFL week number"},
                "season": {"type": "string", "description": "Season year, e.g. '2025'"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Labels like ['sell-high', 'positional-need', 'buy-low']",
                },
            },
            "required": ["decision_type", "title", "decision_made"],
        },
    },
    {
        "name": "grade_journal_entry",
        "description": (
            "Retroactively grade a past journal entry with an outcome grade and notes. "
            "Use this when the user wants to review and score a previous decision."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Journal entry ID from the decision_journal table"},
                "grade": {
                    "type": "string",
                    "enum": ["A", "B", "C", "D", "F"],
                    "description": "Outcome grade: A=excellent, B=good, C=neutral, D=poor, F=disaster",
                },
                "outcome_notes": {"type": "string", "description": "What actually happened as a result"},
            },
            "required": ["id", "grade"],
        },
    },
    {
        "name": "update_player_note",
        "description": (
            "Save or update personal analysis for a player: dynasty grade, buy/sell/hold flag, "
            "trade value score, injury concern, and free-form notes. "
            "These notes appear in roster_overview.md and inform strategic recommendations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "player_id": {"type": "string", "description": "Sleeper player_id"},
                "dynasty_grade": {
                    "type": "string",
                    "enum": ["S", "A", "B", "C", "D"],
                    "description": "S=elite dynasty asset, A=strong, B=solid, C=depth, D=cut candidate",
                },
                "action_flag": {
                    "type": "string",
                    "enum": ["buy", "sell", "hold", "cut", "taxi"],
                    "description": "Current strategic stance on this player",
                },
                "trade_value": {
                    "type": "integer",
                    "description": "Subjective dynasty trade value 1-100",
                },
                "injury_concern": {
                    "type": "string",
                    "enum": ["none", "minor", "major", "chronic"],
                },
                "notes": {"type": "string", "description": "Free-form analysis notes"},
            },
            "required": ["player_id"],
        },
    },
    {
        "name": "get_league_settings",
        "description": "Retrieve the current league settings snapshot: scoring, roster slots, taxi/IR slots, trade deadline, playoff format.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


class ToolHandler:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def dispatch(self, tool_name: str, tool_input: dict) -> str:
        handler = getattr(self, f"_handle_{tool_name}", None)
        if handler is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            return handler(tool_input)
        except Exception as e:
            return json.dumps({"error": str(e)})

    # ------------------------------------------------------------------

    def _handle_query_database(self, inp: dict) -> str:
        sql = inp["sql"].strip()
        limit = inp.get("limit", 100)

        # Safety: only allow SELECT
        normalized = sql.upper().lstrip()
        if not normalized.startswith("SELECT") and not normalized.startswith("WITH"):
            return json.dumps({"error": "Only SELECT queries are allowed."})

        # Inject LIMIT if not present
        if "LIMIT" not in sql.upper() and limit:
            sql = f"{sql.rstrip(';')} LIMIT {limit}"

        conn = get_connection(self.cfg.db_path)
        try:
            cursor = conn.execute(sql)
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            result = {
                "columns": cols,
                "rows": [dict(zip(cols, row)) for row in rows],
                "count": len(rows),
            }
            return json.dumps(result, default=str)
        except sqlite3.Error as e:
            return json.dumps({"error": f"SQL error: {e}"})
        finally:
            conn.close()

    def _handle_sync_data(self, inp: dict) -> str:
        scope = inp.get("scope", "full")
        from ffootball.sync.engine import SyncEngine
        engine = SyncEngine(self.cfg)
        try:
            results = engine.sync(scope=scope)
        finally:
            engine.close()
        return json.dumps({"synced": scope, "results": results})

    def _handle_read_context_file(self, inp: dict) -> str:
        filename = inp["filename"]
        path = self.cfg.context_dir / f"{filename}.md"
        if not path.exists():
            return json.dumps({"error": f"{filename}.md not found — run sync first."})
        return path.read_text()

    def _handle_add_journal_entry(self, inp: dict) -> str:
        conn = get_connection(self.cfg.db_path)
        options = json.dumps(inp.get("options_considered") or [])
        assets = json.dumps(inp.get("assets_involved") or {})
        tags = ",".join(inp.get("tags") or [])
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO decision_journal
                  (created_at, decision_type, title, context, options_considered,
                   decision_made, reasoning, assets_involved, week, season, tags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    inp["decision_type"],
                    inp["title"],
                    inp.get("context"),
                    options,
                    inp["decision_made"],
                    inp.get("reasoning"),
                    assets,
                    inp.get("week"),
                    inp.get("season") or self.cfg.sleeper_season,
                    tags or None,
                ),
            )
            entry_id = cursor.lastrowid
        conn.close()
        return json.dumps({"created": True, "id": entry_id, "title": inp["title"]})

    def _handle_grade_journal_entry(self, inp: dict) -> str:
        entry_id = inp["id"]
        grade = inp["grade"]
        notes = inp.get("outcome_notes", "")
        conn = get_connection(self.cfg.db_path)
        with conn:
            conn.execute(
                """
                UPDATE decision_journal
                SET outcome_grade=?, outcome_notes=?, outcome_date=?
                WHERE id=?
                """,
                (grade, notes, datetime.now(timezone.utc).isoformat(), entry_id),
            )
        conn.close()
        return json.dumps({"graded": True, "id": entry_id, "grade": grade})

    def _handle_update_player_note(self, inp: dict) -> str:
        player_id = inp["player_id"]
        conn = get_connection(self.cfg.db_path)
        with conn:
            conn.execute(
                """
                INSERT INTO player_notes
                  (player_id, dynasty_grade, action_flag, trade_value,
                   injury_concern, notes, updated_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(player_id) DO UPDATE SET
                  dynasty_grade=COALESCE(excluded.dynasty_grade, dynasty_grade),
                  action_flag=COALESCE(excluded.action_flag, action_flag),
                  trade_value=COALESCE(excluded.trade_value, trade_value),
                  injury_concern=COALESCE(excluded.injury_concern, injury_concern),
                  notes=COALESCE(excluded.notes, notes),
                  updated_at=excluded.updated_at
                """,
                (
                    player_id,
                    inp.get("dynasty_grade"),
                    inp.get("action_flag"),
                    inp.get("trade_value"),
                    inp.get("injury_concern"),
                    inp.get("notes"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        conn.close()
        return json.dumps({"updated": True, "player_id": player_id})

    def _handle_get_league_settings(self, inp: dict) -> str:
        conn = get_connection(self.cfg.db_path)
        row = conn.execute(
            "SELECT * FROM league_settings ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            return json.dumps({"error": "No league settings found — run sync first."})
        result = dict(row)
        # Parse JSON columns for readability
        for col in ("roster_positions", "scoring_settings"):
            if result.get(col):
                try:
                    result[col] = json.loads(result[col])
                except Exception:
                    pass
        result.pop("raw_json", None)  # too large to include
        return json.dumps(result, default=str)
