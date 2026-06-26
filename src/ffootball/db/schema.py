import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

DDL = [
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS config (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS league_settings (
        id                  INTEGER PRIMARY KEY,
        league_id           TEXT NOT NULL,
        season              TEXT NOT NULL,
        name                TEXT,
        total_rosters       INTEGER,
        roster_positions    TEXT,
        scoring_settings    TEXT,
        taxi_slots          INTEGER,
        ir_slots            INTEGER,
        trade_deadline      INTEGER,
        playoff_week_start  INTEGER,
        playoff_teams       INTEGER,
        waiver_type         INTEGER,
        waiver_budget       INTEGER,
        raw_json            TEXT,
        synced_at           TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS players (
        player_id         TEXT PRIMARY KEY,
        full_name         TEXT,
        first_name        TEXT,
        last_name         TEXT,
        position          TEXT,
        team              TEXT,
        age               INTEGER,
        years_exp         INTEGER,
        status            TEXT,
        injury_status     TEXT,
        fantasy_positions TEXT,
        raw_json          TEXT,
        synced_at         TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_players_position ON players(position)",
    "CREATE INDEX IF NOT EXISTS idx_players_team ON players(team)",
    "CREATE INDEX IF NOT EXISTS idx_players_name ON players(full_name)",
    """
    CREATE TABLE IF NOT EXISTS roster (
        player_id         TEXT PRIMARY KEY,
        roster_slot       TEXT,
        acquisition_type  TEXT,
        acquisition_date  TEXT,
        acquisition_cost  TEXT,
        synced_at         TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS draft_picks (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        season        TEXT NOT NULL,
        round         INTEGER NOT NULL,
        original_team TEXT,
        current_owner TEXT,
        is_own_pick   INTEGER NOT NULL DEFAULT 0,
        notes         TEXT,
        synced_at     TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_picks_season ON draft_picks(season)",
    """
    CREATE TABLE IF NOT EXISTS matchups (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        season           TEXT NOT NULL,
        week             INTEGER NOT NULL,
        matchup_id       INTEGER,
        roster_id        TEXT NOT NULL,
        is_my_roster     INTEGER NOT NULL DEFAULT 0,
        points           REAL,
        projected_points REAL,
        players_json     TEXT,
        synced_at        TEXT NOT NULL,
        UNIQUE(season, week, roster_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_matchups_week ON matchups(season, week)",
    """
    CREATE TABLE IF NOT EXISTS transactions (
        transaction_id TEXT PRIMARY KEY,
        type           TEXT,
        status         TEXT,
        week           INTEGER,
        season         TEXT,
        roster_ids     TEXT,
        adds           TEXT,
        drops          TEXT,
        draft_picks    TEXT,
        raw_json       TEXT,
        synced_at      TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(type, season)",
    """
    CREATE TABLE IF NOT EXISTS decision_journal (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at         TEXT NOT NULL,
        decision_type      TEXT NOT NULL,
        title              TEXT NOT NULL,
        context            TEXT,
        options_considered TEXT,
        decision_made      TEXT NOT NULL,
        reasoning          TEXT,
        assets_involved    TEXT,
        week               INTEGER,
        season             TEXT,
        outcome_grade      TEXT,
        outcome_notes      TEXT,
        outcome_date       TEXT,
        tags               TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_journal_type ON decision_journal(decision_type)",
    "CREATE INDEX IF NOT EXISTS idx_journal_season ON decision_journal(season, week)",
    """
    CREATE TABLE IF NOT EXISTS player_notes (
        player_id      TEXT PRIMARY KEY,
        dynasty_grade  TEXT,
        action_flag    TEXT,
        trade_value    INTEGER,
        injury_concern TEXT,
        notes          TEXT,
        updated_at     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS context_files (
        filename   TEXT PRIMARY KEY,
        filepath   TEXT NOT NULL,
        line_count INTEGER,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS league_rosters (
        roster_id    TEXT NOT NULL,
        user_id      TEXT,
        team_name    TEXT,
        player_ids   TEXT NOT NULL,  -- JSON array of player_ids on this roster
        synced_at    TEXT NOT NULL,
        PRIMARY KEY (roster_id)
    )
    """,
]

CORE_TABLES = [
    "config", "league_settings", "players", "roster", "draft_picks",
    "matchups", "transactions", "decision_journal", "player_notes",
    "context_files", "league_rosters",
]


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# Migrations to run when upgrading from an older schema version.
# Each entry is (from_version, to_version, list_of_sql_statements).
MIGRATIONS: list[tuple[int, int, list[str]]] = [
    (1, 2, [
        # Added league_rosters table in v2
        """
        CREATE TABLE IF NOT EXISTS league_rosters (
            roster_id    TEXT NOT NULL,
            user_id      TEXT,
            team_name    TEXT,
            player_ids   TEXT NOT NULL,
            synced_at    TEXT NOT NULL,
            PRIMARY KEY (roster_id)
        )
        """,
    ]),
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply any pending schema migrations, updating schema_version after each."""
    from datetime import datetime, timezone

    current = conn.execute(
        "SELECT MAX(version) AS v FROM schema_version"
    ).fetchone()
    current_ver = current["v"] if current and current["v"] else 0

    for from_ver, to_ver, stmts in MIGRATIONS:
        if current_ver < to_ver and current_ver >= from_ver:
            for stmt in stmts:
                conn.execute(stmt)
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (to_ver, datetime.now(timezone.utc).isoformat()),
            )
            current_ver = to_ver


def init_db(db_path: str | Path) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    with conn:
        for stmt in DDL:
            conn.execute(stmt)
        existing = conn.execute(
            "SELECT version FROM schema_version WHERE version = ?", (SCHEMA_VERSION,)
        ).fetchone()
        if not existing:
            from datetime import datetime, timezone
            # Run any pending migrations first (for existing DBs), then stamp final version
            _run_migrations(conn)
            # Stamp the target version if not yet present
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
            )
    conn.close()


def verify_schema(db_path: str | Path) -> dict[str, bool]:
    conn = get_connection(db_path)
    results = {}
    for table in CORE_TABLES:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        results[table] = row is not None
    conn.close()
    return results
