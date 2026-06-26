"""Tests for dynasty valuation analysis module."""
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from ffootball.agent.analysis import (
    age_value_factor,
    compute_player_score,
    pick_value,
    get_ktc_ranking,
    get_roster_analysis,
    analyze_trade,
)
from ffootball.db.schema import init_db, get_connection


# ---------------------------------------------------------------------------
# age_value_factor
# ---------------------------------------------------------------------------

class TestAgeValueFactor:
    def test_rb_peak_age(self):
        assert age_value_factor("RB", 23, 2) >= 0.95

    def test_rb_cliff_at_30(self):
        young = age_value_factor("RB", 23, 2)
        old = age_value_factor("RB", 30, 9)
        assert old < young * 0.5, "RB at 30 should be significantly discounted"

    def test_wr_longer_window_than_rb(self):
        rb_28 = age_value_factor("RB", 28, 7)
        wr_28 = age_value_factor("WR", 28, 7)
        assert wr_28 > rb_28, "WR at 28 should be more valuable than RB at 28"

    def test_qb_long_career(self):
        qb_30 = age_value_factor("QB", 30, 8)
        rb_30 = age_value_factor("RB", 30, 8)
        assert qb_30 > rb_30

    def test_te_slow_developer(self):
        # TE at 22 should be less than peak (25-29)
        young_te = age_value_factor("TE", 22, 1)
        prime_te = age_value_factor("TE", 27, 5)
        assert prime_te > young_te

    def test_unknown_position_returns_float(self):
        val = age_value_factor("K", 25, 3)
        assert isinstance(val, float)
        assert 0 <= val <= 1.2


# ---------------------------------------------------------------------------
# compute_player_score
# ---------------------------------------------------------------------------

class TestComputePlayerScore:
    def test_elite_young_rb_high_score(self):
        score = compute_player_score("RB", 23, 2, "A", None, False)
        assert score >= 70

    def test_old_rb_d_grade_low_score(self):
        score = compute_player_score("RB", 31, 10, "D", None, False)
        assert score < 20

    def test_sf_qb_higher_than_standard(self):
        sf = compute_player_score("QB", 26, 4, "A", None, True)
        std = compute_player_score("QB", 26, 4, "A", None, False)
        assert sf > std

    def test_score_bounds(self):
        for pos in ("QB", "RB", "WR", "TE"):
            for age in (20, 25, 30, 35):
                score = compute_player_score(pos, age, 3, "B", None, False)
                assert 0 <= score <= 100, f"Score out of bounds for {pos} age {age}"

    def test_trade_value_blended(self):
        no_tv = compute_player_score("WR", 25, 3, "B", None, False)
        with_tv = compute_player_score("WR", 25, 3, "B", 90, False)
        # High trade_value should push score up relative to grade-only
        assert with_tv != no_tv


# ---------------------------------------------------------------------------
# pick_value
# ---------------------------------------------------------------------------

class TestPickValue:
    def test_r1_higher_than_r3(self):
        assert pick_value("2025", 1, "2025") > pick_value("2025", 3, "2025")

    def test_future_pick_premium(self):
        current = pick_value("2025", 1, "2025")
        future = pick_value("2026", 1, "2025")
        assert future > current

    def test_far_future_pick_premium_capped(self):
        near = pick_value("2026", 1, "2025")
        far = pick_value("2028", 1, "2025")
        assert far > near


# ---------------------------------------------------------------------------
# DB-dependent tests
# ---------------------------------------------------------------------------

def _make_test_db() -> tuple[Path, object]:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = Path(tmp.name)
    init_db(db)
    conn = get_connection(db)

    # Seed league_settings with a simple 12-team roster
    positions = json.dumps(["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN", "BN", "BN", "BN"])
    conn.execute(
        """
        INSERT INTO league_settings
          (league_id, season, name, total_rosters, roster_positions,
           scoring_settings, taxi_slots, ir_slots, trade_deadline,
           playoff_week_start, playoff_teams, waiver_type, waiver_budget,
           raw_json, synced_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        ("test_league", "2025", "Test League", 12, positions,
         json.dumps({"rec": 1.0}), 2, 1, 11, 15, 6, 2, 100,
         json.dumps({}), "2025-01-01T00:00:00+00:00"),
    )

    # Seed players
    players = [
        ("p1", "Josh Allen", "QB", "BUF", 28, 6),
        ("p2", "Breece Hall", "RB", "NYJ", 23, 3),
        ("p3", "Austin Ekeler", "RB", "LAC", 29, 7),
        ("p4", "Justin Jefferson", "WR", "MIN", 25, 4),
        ("p5", "Davante Adams", "WR", "LV", 32, 10),
        ("p6", "Sam LaPorta", "TE", "DET", 23, 2),
    ]
    for pid, name, pos, team, age, exp in players:
        conn.execute(
            """
            INSERT INTO players (player_id, full_name, position, team, age,
                                  years_exp, status, synced_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (pid, name, pos, team, age, exp, "Active", "2025-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO roster (player_id, roster_slot, synced_at) VALUES (?,?,?)",
            (pid, "BN", "2025-01-01T00:00:00+00:00"),
        )

    # Seed player notes
    grades = {"p1": ("A", "hold"), "p2": ("A", "hold"), "p3": ("C", "sell"),
              "p4": ("S", "hold"), "p5": ("D", "cut"), "p6": ("B", "hold")}
    for pid, (grade, flag) in grades.items():
        conn.execute(
            """INSERT INTO player_notes (player_id, dynasty_grade, action_flag, updated_at)
               VALUES (?,?,?,?)""",
            (pid, grade, flag, "2025-01-01T00:00:00+00:00"),
        )

    conn.commit()
    return db, conn


class TestGetKTCRanking:
    def test_returns_ranked_list(self):
        db, conn = _make_test_db()
        ranking = get_ktc_ranking(conn, is_superflex=False)
        assert len(ranking) == 6
        conn.close()

    def test_ranked_by_score_descending(self):
        db, conn = _make_test_db()
        ranking = get_ktc_ranking(conn, is_superflex=False)
        scores = [r.dynasty_score for r in ranking]
        assert scores == sorted(scores, reverse=True)
        conn.close()

    def test_position_filter(self):
        db, conn = _make_test_db()
        ranking = get_ktc_ranking(conn, is_superflex=False)
        wr_only = [r for r in ranking if r.position == "WR"]
        assert len(wr_only) == 2
        conn.close()

    def test_old_player_ranks_lower(self):
        db, conn = _make_test_db()
        ranking = get_ktc_ranking(conn, is_superflex=False)
        names = [r.name for r in ranking]  # RankedPlayer uses .name not .full_name
        # Davante Adams (D grade, 32yo) should rank below Justin Jefferson (S grade, 25yo)
        jeff_rank = names.index("Justin Jefferson")
        adams_rank = names.index("Davante Adams")
        assert jeff_rank < adams_rank
        conn.close()


class TestGetRosterAnalysis:
    def test_returns_analysis(self):
        db, conn = _make_test_db()
        analysis = get_roster_analysis(conn, is_superflex=False)
        assert analysis is not None
        conn.close()

    def test_sell_flag_for_old_rb(self):
        db, conn = _make_test_db()
        # Seed Austin Ekeler with a "sell" action flag so he shows up in sell_flags
        conn.execute(
            "UPDATE player_notes SET action_flag='sell' WHERE player_id='p3'"
        )
        conn.commit()
        analysis = get_roster_analysis(conn, is_superflex=False)
        # RosterAnalysis.sell_flags is a list of player name strings
        assert any("Ekeler" in n for n in analysis.sell_flags), \
            f"Expected Ekeler in sell_flags, got: {analysis.sell_flags}"
        conn.close()


class TestAnalyzeTrade:
    def test_basic_trade_returns_result(self):
        db, conn = _make_test_db()
        result = analyze_trade(
            conn,
            players_giving=["p3"],  # Austin Ekeler (old RB)
            players_receiving=["p6"],  # Sam LaPorta (young TE)
            picks_giving=[],
            picks_receiving=[],
            current_season="2025",
            is_superflex=False,
        )
        assert result is not None
        assert result.win_margin in ("big win", "slight win", "even", "slight loss", "big loss")
        conn.close()

    def test_giving_elite_for_nothing_is_loss(self):
        db, conn = _make_test_db()
        result = analyze_trade(
            conn,
            players_giving=["p4"],  # Justin Jefferson (S grade, 25)
            players_receiving=["p5"],  # Davante Adams (D grade, 32)
            picks_giving=[],
            picks_receiving=[],
            current_season="2025",
            is_superflex=False,
        )
        assert result.delta < 0, "Giving elite player for washed one should be negative delta"
        conn.close()
