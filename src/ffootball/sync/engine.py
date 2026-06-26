"""
Sync engine — pulls data from Sleeper API into local SQLite database.

Scopes:
  full         — everything below in order
  league       — settings only
  players      — bulk player universe (skipped if synced within 24h)
  roster       — your roster + draft picks
  matchups     — current week matchups
  transactions — last 4 weeks of transactions
"""
import json
from datetime import datetime, timezone

from ffootball.config import Config
from ffootball.db.queries import get_config_value, set_config_value
from ffootball.db.schema import get_connection
from ffootball.sleeper import client as sleeper


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str) -> None:
    print(f"  {msg}")


class SyncEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.conn = get_connection(cfg.db_path)

    def close(self) -> None:
        self.conn.close()

    def sync(self, scope: str = "full") -> dict[str, int]:
        """
        Run a sync for the given scope. Returns a summary dict of row counts.
        """
        results: dict[str, int] = {}

        if scope in ("full", "league"):
            print("Syncing league settings...")
            results["league_settings"] = self._sync_league_settings()

        if scope in ("full", "players"):
            print("Syncing player universe...")
            results["players"] = self._sync_players()

        if scope in ("full", "roster"):
            print("Syncing roster and draft picks...")
            r, p = self._sync_roster()
            results["roster"] = r
            results["draft_picks"] = p

        if scope in ("full", "matchups"):
            print("Syncing matchups...")
            results["matchups"] = self._sync_matchups()

        if scope in ("full", "transactions"):
            print("Syncing transactions...")
            results["transactions"] = self._sync_transactions()

        if scope == "full":
            with self.conn:
                set_config_value(self.conn, "last_sync_full", _now())

        # Regenerate context markdown after any sync
        print("Regenerating context files...")
        try:
            from ffootball.context.generator import ContextGenerator
            gen = ContextGenerator(self.cfg)
            ctx_results = gen.generate_all()
            gen.close()
            for fname, line_count in ctx_results.items():
                _log(f"{fname}: {line_count} lines")
        except Exception as e:
            _log(f"context generation warning: {e}")

        return results

    # ------------------------------------------------------------------
    # League settings
    # ------------------------------------------------------------------

    def _sync_league_settings(self) -> int:
        league_id = self.cfg.sleeper_league_id
        data = sleeper.get_league(league_id)

        scoring = data.get("scoring_settings", {})
        roster_pos = data.get("roster_positions", [])
        settings = data.get("settings", {})

        with self.conn:
            self.conn.execute("DELETE FROM league_settings WHERE league_id=?", (league_id,))
            self.conn.execute(
                """
                INSERT INTO league_settings
                  (league_id, season, name, total_rosters, roster_positions,
                   scoring_settings, taxi_slots, ir_slots, trade_deadline,
                   playoff_week_start, playoff_teams, waiver_type, waiver_budget,
                   raw_json, synced_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    league_id,
                    data.get("season", self.cfg.sleeper_season),
                    data.get("name"),
                    data.get("total_rosters"),
                    json.dumps(roster_pos),
                    json.dumps(scoring),
                    settings.get("taxi_slots", 0),
                    settings.get("reserve_slots", 0),
                    settings.get("trade_deadline", 0),
                    settings.get("playoff_week_start", 15),
                    settings.get("playoff_teams", 6),
                    settings.get("waiver_type", 0),
                    settings.get("waiver_budget", 100),
                    json.dumps(data),
                    _now(),
                ),
            )
            set_config_value(self.conn, "last_sync_league", _now())
            # Cache user's own roster_id for future use
            self._cache_my_roster_id()

        _log(f"League: {data.get('name')} ({data.get('season')})")
        return 1

    def _cache_my_roster_id(self) -> None:
        """Identify and cache your roster_id by matching user_id."""
        user_id = get_config_value(self.conn, "user_id")
        if not user_id:
            return
        rosters = sleeper.get_rosters(self.cfg.sleeper_league_id)
        for r in rosters:
            if r.get("owner_id") == user_id:
                set_config_value(self.conn, "my_roster_id", str(r["roster_id"]))
                return

    # ------------------------------------------------------------------
    # Players (bulk)
    # ------------------------------------------------------------------

    def _sync_players(self) -> int:
        # Skip if synced within the last 20 hours
        last = get_config_value(self.conn, "last_sync_players")
        if last:
            age_hours = (
                datetime.now(timezone.utc) - datetime.fromisoformat(last)
            ).total_seconds() / 3600
            if age_hours < 20:
                _log(f"Players: skipping (synced {age_hours:.1f}h ago)")
                return 0

        _log("Fetching player universe from Sleeper (~4MB)...")
        players = sleeper.get_all_players()
        count = 0

        with self.conn:
            for player_id, p in players.items():
                fp = p.get("fantasy_positions") or []
                self.conn.execute(
                    """
                    INSERT INTO players
                      (player_id, full_name, first_name, last_name, position,
                       team, age, years_exp, status, injury_status,
                       fantasy_positions, raw_json, synced_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(player_id) DO UPDATE SET
                      full_name=excluded.full_name,
                      first_name=excluded.first_name,
                      last_name=excluded.last_name,
                      position=excluded.position,
                      team=excluded.team,
                      age=excluded.age,
                      years_exp=excluded.years_exp,
                      status=excluded.status,
                      injury_status=excluded.injury_status,
                      fantasy_positions=excluded.fantasy_positions,
                      raw_json=excluded.raw_json,
                      synced_at=excluded.synced_at
                    """,
                    (
                        player_id,
                        p.get("full_name"),
                        p.get("first_name"),
                        p.get("last_name"),
                        p.get("position"),
                        p.get("team"),
                        p.get("age"),
                        p.get("years_exp"),
                        p.get("status"),
                        p.get("injury_status"),
                        json.dumps(fp),
                        json.dumps(p),
                        _now(),
                    ),
                )
                count += 1
            set_config_value(self.conn, "last_sync_players", _now())

        _log(f"Players: upserted {count:,}")
        return count

    # ------------------------------------------------------------------
    # Roster
    # ------------------------------------------------------------------

    def _sync_roster(self) -> tuple[int, int]:
        """Returns (roster_count, picks_count)."""
        league_id = self.cfg.sleeper_league_id
        user_id = get_config_value(self.conn, "user_id")
        if not user_id:
            _log("roster: user_id not set — run setup first")
            return 0, 0

        rosters = sleeper.get_rosters(league_id)
        my_roster = next(
            (r for r in rosters if r.get("owner_id") == user_id), None
        )
        if not my_roster:
            _log(f"roster: no roster found for user_id={user_id}")
            return 0, 0

        roster_id = str(my_roster["roster_id"])
        player_ids: list[str] = my_roster.get("players") or []
        taxi_ids: set[str] = set(my_roster.get("taxi") or [])
        ir_ids: set[str] = set(my_roster.get("reserve") or [])
        starters: list[str] = my_roster.get("starters") or []

        now = _now()
        with self.conn:
            self.conn.execute("DELETE FROM roster")
            for pid in player_ids:
                if pid in taxi_ids:
                    slot = "TAXI"
                elif pid in ir_ids:
                    slot = "IR"
                elif pid in starters:
                    slot = "STARTER"
                else:
                    slot = "BN"
                self.conn.execute(
                    """
                    INSERT INTO roster (player_id, roster_slot, acquisition_type,
                                        acquisition_date, acquisition_cost, synced_at)
                    VALUES (?,?,?,?,?,?)
                    ON CONFLICT(player_id) DO UPDATE SET
                      roster_slot=excluded.roster_slot,
                      synced_at=excluded.synced_at
                    """,
                    (pid, slot, None, None, None, now),
                )
            set_config_value(self.conn, "last_sync_roster", now)
            set_config_value(self.conn, "my_roster_id", roster_id)

        roster_count = len(player_ids)
        _log(f"Roster: {roster_count} players (taxi={len(taxi_ids)}, IR={len(ir_ids)})")

        # Draft picks
        picks_count = self._sync_draft_picks(league_id, roster_id)
        return roster_count, picks_count

    def _sync_draft_picks(self, league_id: str, my_roster_id: str) -> int:
        traded_picks = sleeper.get_traded_picks(league_id)
        rosters = sleeper.get_rosters(league_id)
        # Build map: roster_id → team name (from users if available)
        roster_map = {str(r["roster_id"]): r for r in rosters}

        now = _now()
        count = 0
        with self.conn:
            self.conn.execute("DELETE FROM draft_picks")
            for pick in traded_picks:
                owner_id = str(pick.get("owner_id", ""))
                is_mine = owner_id == my_roster_id
                original = str(pick.get("roster_id", ""))
                orig_name = roster_map.get(original, {}).get("owner_id", original)
                self.conn.execute(
                    """
                    INSERT INTO draft_picks
                      (season, round, original_team, current_owner, is_own_pick, synced_at)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (
                        str(pick.get("season", "")),
                        pick.get("round", 0),
                        orig_name,
                        owner_id,
                        1 if is_mine else 0,
                        now,
                    ),
                )
                count += 1
            set_config_value(self.conn, "last_sync_picks", now)

        _log(f"Draft picks: {count} traded picks tracked ({sum(1 for p in traded_picks if str(p.get('owner_id','')) == my_roster_id)} yours)")
        return count

    # ------------------------------------------------------------------
    # Matchups
    # ------------------------------------------------------------------

    def _sync_matchups(self) -> int:
        league_id = self.cfg.sleeper_league_id
        my_roster_id = get_config_value(self.conn, "my_roster_id")

        # Get current NFL week from Sleeper state
        try:
            nfl_state = sleeper.get_nfl_state()
            current_week = nfl_state.get("display_week") or nfl_state.get("week") or 1
            season = str(nfl_state.get("season", self.cfg.sleeper_season))
        except Exception:
            current_week = 1
            season = self.cfg.sleeper_season

        matchups = sleeper.get_matchups(league_id, current_week)
        now = _now()
        count = 0

        with self.conn:
            for m in matchups:
                rid = str(m.get("roster_id", ""))
                is_mine = 1 if rid == my_roster_id else 0
                players_pts = m.get("players_points") or {}
                self.conn.execute(
                    """
                    INSERT INTO matchups
                      (season, week, matchup_id, roster_id, is_my_roster,
                       points, projected_points, players_json, synced_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(season, week, roster_id) DO UPDATE SET
                      matchup_id=excluded.matchup_id,
                      points=excluded.points,
                      projected_points=excluded.projected_points,
                      players_json=excluded.players_json,
                      synced_at=excluded.synced_at
                    """,
                    (
                        season,
                        current_week,
                        m.get("matchup_id"),
                        rid,
                        is_mine,
                        m.get("points"),
                        m.get("custom_points"),
                        json.dumps(players_pts),
                        now,
                    ),
                )
                count += 1
            set_config_value(self.conn, "last_sync_matchups", now)

        _log(f"Matchups: week {current_week}, {count} roster entries")
        return count

    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------

    def _sync_transactions(self, weeks: int = 4) -> int:
        league_id = self.cfg.sleeper_league_id
        txns = sleeper.get_transactions_range(league_id, weeks=weeks)
        now = _now()
        count = 0

        with self.conn:
            for t in txns:
                tid = t.get("transaction_id") or f"{t.get('leg',0)}_{t.get('created',0)}"
                self.conn.execute(
                    """
                    INSERT INTO transactions
                      (transaction_id, type, status, week, season,
                       roster_ids, adds, drops, draft_picks, raw_json, synced_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(transaction_id) DO UPDATE SET
                      status=excluded.status,
                      raw_json=excluded.raw_json,
                      synced_at=excluded.synced_at
                    """,
                    (
                        str(tid),
                        t.get("type"),
                        t.get("status"),
                        t.get("leg"),
                        self.cfg.sleeper_season,
                        json.dumps(t.get("roster_ids") or []),
                        json.dumps(t.get("adds") or {}),
                        json.dumps(t.get("drops") or {}),
                        json.dumps(t.get("draft_picks") or []),
                        json.dumps(t),
                        now,
                    ),
                )
                count += 1
            set_config_value(self.conn, "last_sync_transactions", now)

        _log(f"Transactions: {count} records (last {weeks} weeks)")
        return count
