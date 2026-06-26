# CLAUDE.md — FFootball Codebase Guide

This file explains the FFootball project to Claude Code sessions picking it up cold.

## What This Is

A CLI conversational agent for dynasty fantasy football, backed by a local SQLite database.
The user runs `ffootball chat` to talk with a Claude Opus 4.8 agent that answers questions
about their Sleeper league, analyzes trades, recommends roster moves, and keeps a decision journal.

---

## Architecture in One Pass

```
User types → __main__.py (click CLI)
               ↓
          agent/agent.py  — REPL + tool-use loop (raw urllib to Anthropic API)
               ↓
          agent/tools.py  — 10 tool handlers (read-only DB + write journal/notes)
               ↓
          agent/analysis.py — dynasty valuation math (age curves, trade scoring)
               ↓
          db/schema.py   — SQLite connection (WAL, Row factory)
               ↓
          data/dynasty.db  — 11 tables (gitignored)
```

Sync path (separate from chat):

```
sync/engine.py → sleeper/client.py (urllib) → Sleeper REST API
                                             → data/dynasty.db
                                             → context/generator.py → context/*.md
```

---

## Key Files

| File | What it does |
|---|---|
| `src/ffootball/__main__.py` | Click CLI: `setup`, `sync`, `chat`, `doctor` commands. Uses `rich` + `click`. |
| `src/ffootball/config.py` | Loads `.env`. Falls back to manual parsing if python-dotenv not installed. |
| `src/ffootball/db/schema.py` | All `CREATE TABLE` DDL, `init_db()`, `verify_schema()`, migration runner. |
| `src/ffootball/db/queries.py` | `get_config_value` / `set_config_value` helpers. |
| `src/ffootball/sleeper/client.py` | Sleeper REST API wrapper using `urllib.request` (no httpx). |
| `src/ffootball/sync/engine.py` | Sync orchestrator. Writes DB tables, then calls context generators. |
| `src/ffootball/context/generator.py` | Generates `roster_overview.md` and `season_context.md` (≤250 lines each). |
| `src/ffootball/agent/agent.py` | Claude Opus 4.8 REPL. Raw urllib POST to Anthropic API, tool-use loop. |
| `src/ffootball/agent/tools.py` | 10 tool definitions + `ToolHandler.dispatch()`. |
| `src/ffootball/agent/analysis.py` | Age-curve math, trade value scoring, KTC ranking, positional needs. |
| `src/ffootball/agent/prompts.py` | System prompt with dynasty GM persona + `build_system_prompt()`. |
| `src/ffootball/log.py` | `setup_logging()` / `get_logger()`. Writes to `data/agent.log`. |

---

## Runtime Dependencies

**Required (pip install or uv sync):** `anthropic`, `httpx`, `python-dotenv`, `rich`, `click`

**However:** the code was written to work with stdlib fallbacks:
- `urllib.request` used for all HTTP (Sleeper + Anthropic API calls) instead of httpx
- Manual `.env` parsing fallback if python-dotenv not installed
- ANSI escape codes instead of rich in `agent.py` (the REPL)
- `__main__.py` uses `rich` / `click` (no fallback there — install the package for the CLI)

---

## Database

- Path: `data/dynasty.db` (relative to project root), configurable via `FFOOTBALL_DB_PATH`
- Connection: WAL journal mode, `sqlite3.Row` factory, `PRAGMA foreign_keys = ON`
- Schema version: 2 (stored in `schema_version` table)
- `init_db()` is idempotent — safe to re-run; runs pending migrations automatically

**11 tables:** `schema_version`, `config`, `league_settings`, `players`, `roster`,
`draft_picks`, `matchups`, `transactions`, `decision_journal`, `player_notes`,
`context_files`, `league_rosters`

Note: `roster` and `player_notes` have no FK constraints to `players` so notes can be
added before a player sync.

---

## Tool-Use Loop

`agent.py::run_chat()` keeps a rolling `messages` list (last 20 turns). Each turn:

1. POST to `https://api.anthropic.com/v1/messages`
2. If `stop_reason == "tool_use"` — dispatch all tool calls in `content`, append results as a `user` message with `tool_result` blocks
3. Loop back to step 1 until `stop_reason == "end_turn"`

Tool dispatch goes through `ToolHandler.dispatch(tool_name, tool_input)` → returns JSON string.

---

## Agent Tools (10 total)

| Tool | Write? | Notes |
|---|---|---|
| `query_database` | No | SELECT-only guard; auto-adds LIMIT if missing |
| `sync_data` | No | Triggers SyncEngine.sync(scope) |
| `read_context_file` | No | Reads context/*.md files |
| `add_journal_entry` | Yes | Inserts into decision_journal |
| `grade_journal_entry` | Yes | Updates outcome_grade + outcome_notes |
| `update_player_note` | Yes | Upserts player_notes |
| `get_league_settings` | No | Returns structured JSON from league_settings |
| `search_journal` | No | LIKE search on journal with filters |
| `analyze_trade` | No | Calls analysis.analyze_trade() |
| `get_roster_analysis` | No | Calls analysis.get_roster_analysis() |
| `get_ktc_ranking` | No | Calls analysis.get_ktc_ranking(), optional position_filter |

---

## Dynasty Valuation Model (analysis.py)

- **Age curves by position:** RB peaks 22-25 (cliff at 30), WR peaks 24-27, QB peaks 27-31, TE peaks 25-29
- **Score = base_grade × age_factor × position_scarcity** (0–100 scale)
- **Superflex detection:** reads `roster_positions` JSON from `league_settings` for "SUPER_FLEX"; applies 1.30x QB scarcity multiplier (vs 0.85x standard)
- **Pick values:** R1=72, R2=48, R3=28 base; +4 pts/year into future

---

## Config / Environment

All runtime config comes from `.env` (project root). Required keys:

```
ANTHROPIC_API_KEY=sk-ant-...
SLEEPER_USERNAME=your_sleeper_handle
SLEEPER_LEAGUE_ID=123456789
SLEEPER_SEASON=2025
```

Optional: `FFOOTBALL_DB_PATH=/custom/path/to/dynasty.db`

---

## Common Tasks for Future Sessions

**Add a new agent tool:**
1. Add schema entry to `TOOL_DEFINITIONS` list in `agent/tools.py`
2. Add `_handle_<toolname>` method to `ToolHandler`
3. Add dispatch case in `ToolHandler.dispatch()`
4. Document in `SYSTEM_PROMPT` in `agent/prompts.py`

**Add a new DB table:**
1. Add DDL to `DDL` list in `db/schema.py`
2. Add table name to `CORE_TABLES`
3. If upgrading an existing schema: add a migration tuple to `MIGRATIONS`
4. Bump `SCHEMA_VERSION`

**Add a new slash command:**
1. Add handler in `_handle_slash()` in `agent/agent.py`
2. Update the `/help` text in the same function

---

## Testing

```bash
uv run pytest tests/ -v
```

Tests use sqlite3 in-memory DBs. No network calls (Sleeper and Anthropic APIs are mocked
or skipped in tests).

---

## Git Branch

Active development branch: `claude/sleeper-dynasty-agent-08jhlw`
