"""
Dynasty valuation and strategic analysis engine.

Provides data-driven analysis for trade decisions, KTC rankings, positional
needs, and waiver targets. All functions take a sqlite3.Connection so they
compose cleanly with the tool handler.
"""
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Age / value decay curves
# Multiplier applied to a player's base grade score.
# Returns a float in [0.0, 1.5] where 1.0 = peak value, <1.0 = declining.
# ---------------------------------------------------------------------------

def age_value_factor(position: str, age: Optional[int], years_exp: Optional[int]) -> float:
    """
    Dynasty age-value multiplier. Reflects expected remaining career value,
    not current-season fantasy output.
    """
    if age is None:
        # Estimate from experience if age missing
        if years_exp is not None:
            age = 21 + years_exp
        else:
            return 1.0  # unknown — neutral

    pos = (position or "").upper()

    if pos == "QB":
        # QBs peak late, careers run long. Discount rookies slightly (system takes time).
        if age <= 23:   return 1.15  # young QB — high upside, some risk
        if age <= 27:   return 1.25  # ascending
        if age <= 31:   return 1.10  # established starter
        if age <= 34:   return 0.85  # late prime
        if age <= 37:   return 0.55  # declining
        return 0.30

    elif pos == "RB":
        # Steepest decay curve. RBs have the shortest dynasty windows.
        if age <= 21:   return 1.40  # rookie premium
        if age <= 23:   return 1.35  # ascending
        if age <= 25:   return 1.15  # peak
        if age <= 27:   return 0.85  # early decline
        if age <= 29:   return 0.55  # steep decline
        if age <= 30:   return 0.35  # cut territory
        return 0.15

    elif pos == "WR":
        # Longer windows. Rookie WRs often have development curves.
        if age <= 21:   return 1.20  # high upside
        if age <= 23:   return 1.30  # ascending fast
        if age <= 27:   return 1.20  # peak
        if age <= 29:   return 1.05  # late prime
        if age <= 31:   return 0.80  # early decline
        if age <= 33:   return 0.55  # declining
        return 0.30

    elif pos == "TE":
        # TEs develop slowly. Late bloomers common (age 25-27 breakout).
        if age <= 22:   return 0.90  # often raw
        if age <= 25:   return 1.10  # developing
        if age <= 29:   return 1.20  # prime
        if age <= 31:   return 1.00  # solid vet
        if age <= 33:   return 0.70  # declining
        return 0.40

    # K, DEF, FLEX — dynasty value is minimal, return neutral
    return 0.80


# ---------------------------------------------------------------------------
# Base grade → numeric score
# ---------------------------------------------------------------------------

GRADE_SCORES = {
    "S": 92, "A": 78, "B": 62, "C": 42, "D": 20,
}
_UNGRADED_DEFAULT = 50  # neutral when no personal grade set


def _grade_to_score(grade: Optional[str]) -> int:
    return GRADE_SCORES.get((grade or "").upper(), _UNGRADED_DEFAULT)


# ---------------------------------------------------------------------------
# Position scarcity multiplier (dynasty context)
# Superflex leagues: QB scarcity is much higher.
# ---------------------------------------------------------------------------

def _pos_scarcity(position: str, is_superflex: bool = False) -> float:
    pos = (position or "").upper()
    if pos == "QB":
        return 1.30 if is_superflex else 0.85
    if pos == "RB": return 1.15
    if pos == "WR": return 1.00
    if pos == "TE": return 1.05
    return 0.75


# ---------------------------------------------------------------------------
# Composite dynasty value score (0–100)
# ---------------------------------------------------------------------------

def compute_player_score(
    position: str,
    age: Optional[int],
    years_exp: Optional[int],
    grade: Optional[str],
    trade_value: Optional[int],
    is_superflex: bool = False,
) -> int:
    """
    Composite dynasty value score. Combines personal grade, age curve,
    and positional scarcity. Trade value override is blended in if set.
    """
    base = _grade_to_score(grade)
    age_factor = age_value_factor(position, age, years_exp)
    scarcity = _pos_scarcity(position, is_superflex)
    computed = base * age_factor * scarcity

    if trade_value is not None:
        # Blend personal grade (60%) with explicit trade value (40%)
        computed = computed * 0.6 + trade_value * 0.4

    return max(0, min(100, round(computed)))


# ---------------------------------------------------------------------------
# Pick valuation (dynasty context)
# ---------------------------------------------------------------------------

_PICK_BASE = {1: 72, 2: 48, 3: 28, 4: 18, 5: 10}
_FUTURE_YEAR_PREMIUM = 4  # per year in the future (uncertainty = upside)


def pick_value(season: str, round_: int, current_season: str) -> int:
    """
    Estimate dynasty value of a draft pick (0–100 scale).
    Future picks get a slight premium for upside uncertainty.
    """
    base = _PICK_BASE.get(round_, 8)
    try:
        years_out = int(season) - int(current_season)
    except (ValueError, TypeError):
        years_out = 0
    bonus = max(0, years_out) * _FUTURE_YEAR_PREMIUM
    return max(0, min(100, base + bonus))


# ---------------------------------------------------------------------------
# Positional needs analysis
# ---------------------------------------------------------------------------

@dataclass
class PositionalNeeds:
    position: str
    on_roster: int
    target_min: int
    target_max: int
    surplus_deficit: int  # positive = surplus, negative = need
    verdict: str          # "surplus" | "adequate" | "thin" | "critical"


_DYNASTY_TARGETS = {
    # (min, max) active + bench targets for each position in a typical 1QB 12-team dynasty
    "QB":  (2, 3),
    "RB":  (6, 9),
    "WR":  (7, 10),
    "TE":  (2, 3),
    "K":   (0, 1),
    "DEF": (0, 1),
}

_SUPERFLEX_QB_TARGETS = (3, 5)


def analyze_positional_needs(
    conn: sqlite3.Connection,
    roster_positions: list[str],
    is_superflex: bool = False,
) -> list[PositionalNeeds]:
    rows = conn.execute(
        """
        SELECT p.position, COUNT(*) as cnt
        FROM roster r
        JOIN players p ON r.player_id = p.player_id
        WHERE p.position IS NOT NULL
        GROUP BY p.position
        """
    ).fetchall()

    counts = {r["position"]: r["cnt"] for r in rows}
    results = []

    targets = dict(_DYNASTY_TARGETS)
    if is_superflex:
        targets["QB"] = _SUPERFLEX_QB_TARGETS

    for pos, (mn, mx) in targets.items():
        count = counts.get(pos, 0)
        diff = count - mn
        if diff >= (mx - mn):
            verdict = "surplus"
        elif diff >= 0:
            verdict = "adequate"
        elif diff >= -1:
            verdict = "thin"
        else:
            verdict = "critical"
        results.append(PositionalNeeds(pos, count, mn, mx, count - mn, verdict))

    return results


# ---------------------------------------------------------------------------
# KTC-style ranking of your roster
# ---------------------------------------------------------------------------

@dataclass
class RankedPlayer:
    player_id: str
    name: str
    position: str
    team: str
    age: Optional[int]
    years_exp: Optional[int]
    grade: Optional[str]
    action_flag: Optional[str]
    dynasty_score: int
    age_factor: float
    roster_slot: str
    notes: str = ""


def get_ktc_ranking(conn: sqlite3.Connection, is_superflex: bool = False) -> list[RankedPlayer]:
    """
    Rank all rostered players by dynasty value score (descending).
    Combines personal grade, age curve, positional scarcity.
    """
    rows = conn.execute(
        """
        SELECT
            r.player_id, r.roster_slot,
            p.full_name, p.position, p.team, p.age, p.years_exp,
            pn.dynasty_grade, pn.action_flag, pn.trade_value, pn.notes
        FROM roster r
        LEFT JOIN players p ON r.player_id = p.player_id
        LEFT JOIN player_notes pn ON r.player_id = pn.player_id
        """
    ).fetchall()

    ranked = []
    for row in rows:
        pos = row["position"] or ""
        age = row["age"]
        exp = row["years_exp"]
        grade = row["dynasty_grade"]
        tv = row["trade_value"]
        score = compute_player_score(pos, age, exp, grade, tv, is_superflex)
        af = age_value_factor(pos, age, exp)
        ranked.append(RankedPlayer(
            player_id=row["player_id"],
            name=row["full_name"] or row["player_id"],
            position=pos,
            team=row["team"] or "FA",
            age=age,
            years_exp=exp,
            grade=grade,
            action_flag=row["action_flag"],
            dynasty_score=score,
            age_factor=round(af, 2),
            roster_slot=row["roster_slot"] or "BN",
            notes=(row["notes"] or "")[:80],
        ))

    ranked.sort(key=lambda p: p.dynasty_score, reverse=True)
    return ranked


# ---------------------------------------------------------------------------
# Trade analysis
# ---------------------------------------------------------------------------

@dataclass
class TradeAnalysis:
    giving_players: list[dict]
    receiving_players: list[dict]
    giving_picks: list[dict]
    receiving_picks: list[dict]
    giving_total: int
    receiving_total: int
    delta: int            # receiving_total - giving_total
    win_margin: str       # "big win" | "slight win" | "even" | "slight loss" | "big loss"
    positional_impact: dict[str, int]  # position → net change in count
    verdict: str
    concerns: list[str]
    positives: list[str]


def analyze_trade(
    conn: sqlite3.Connection,
    players_giving: list[str],
    players_receiving: list[str],
    picks_giving: list[dict],
    picks_receiving: list[dict],
    current_season: str = "2025",
    is_superflex: bool = False,
) -> TradeAnalysis:
    """
    Compute a structured trade analysis.

    players_giving / players_receiving: list of Sleeper player_ids
    picks_giving / picks_receiving: list of {"season": "2025", "round": 2}
    """
    def _fetch_player(pid: str) -> dict:
        row = conn.execute(
            """
            SELECT p.player_id, p.full_name, p.position, p.team, p.age, p.years_exp,
                   pn.dynasty_grade, pn.action_flag, pn.trade_value, pn.notes
            FROM players p
            LEFT JOIN player_notes pn ON p.player_id = pn.player_id
            WHERE p.player_id = ?
            """,
            (pid,),
        ).fetchone()
        if not row:
            return {"player_id": pid, "full_name": pid, "position": "?", "age": None,
                    "years_exp": None, "dynasty_grade": None, "trade_value": None}
        return dict(row)

    giving_data = [_fetch_player(pid) for pid in players_giving]
    receiving_data = [_fetch_player(pid) for pid in players_receiving]

    # Score players
    for p in giving_data + receiving_data:
        p["score"] = compute_player_score(
            p.get("position", ""),
            p.get("age"),
            p.get("years_exp"),
            p.get("dynasty_grade"),
            p.get("trade_value"),
            is_superflex,
        )

    # Score picks
    giving_pick_data = []
    for pk in picks_giving:
        season = str(pk.get("season", current_season))
        round_ = int(pk.get("round", 2))
        val = pick_value(season, round_, current_season)
        giving_pick_data.append({
            "label": f"{season} Round {round_}",
            "season": season, "round": round_, "score": val,
        })

    receiving_pick_data = []
    for pk in picks_receiving:
        season = str(pk.get("season", current_season))
        round_ = int(pk.get("round", 2))
        val = pick_value(season, round_, current_season)
        receiving_pick_data.append({
            "label": f"{season} Round {round_}",
            "season": season, "round": round_, "score": val,
        })

    giving_total = (
        sum(p["score"] for p in giving_data) +
        sum(p["score"] for p in giving_pick_data)
    )
    receiving_total = (
        sum(p["score"] for p in receiving_data) +
        sum(p["score"] for p in receiving_pick_data)
    )

    delta = receiving_total - giving_total

    if delta >= 20:    win_margin = "big win"
    elif delta >= 8:   win_margin = "slight win"
    elif delta >= -7:  win_margin = "even"
    elif delta >= -19: win_margin = "slight loss"
    else:              win_margin = "big loss"

    # Positional impact (net change on your roster)
    pos_impact: dict[str, int] = {}
    for p in receiving_data:
        pos = (p.get("position") or "?").upper()
        pos_impact[pos] = pos_impact.get(pos, 0) + 1
    for p in giving_data:
        pos = (p.get("position") or "?").upper()
        pos_impact[pos] = pos_impact.get(pos, 0) - 1

    # Generate concerns and positives
    concerns = []
    positives = []

    if delta < -15:
        concerns.append(f"Significant value gap: you give {giving_total} pts, receive {receiving_total} pts")

    # Age concerns on incoming players
    for p in receiving_data:
        age = p.get("age")
        pos = (p.get("position") or "").upper()
        if age and pos == "RB" and age >= 28:
            concerns.append(f"{p.get('full_name')} is {age} — late-career RB with short dynasty window")
        if age and pos in ("WR", "TE") and age >= 32:
            concerns.append(f"{p.get('full_name')} is {age} — limited remaining dynasty value")

    # Selling young players
    for p in giving_data:
        age = p.get("age")
        pos = (p.get("position") or "").upper()
        af = age_value_factor(pos, age, p.get("years_exp"))
        if af >= 1.20 and p.get("score", 0) >= 60:
            concerns.append(f"Selling {p.get('full_name')} in their prime — consider long-term impact")

    # Positional needs filled
    for pos, change in pos_impact.items():
        if change > 0 and pos in ("RB", "WR", "QB"):
            positives.append(f"Adds depth at {pos}")

    # Receiving a sell-high player
    for p in receiving_data:
        if p.get("action_flag") == "buy":
            positives.append(f"{p.get('full_name')} is flagged as a buy — acquiring at fair value")
    for p in giving_data:
        if p.get("action_flag") == "sell":
            positives.append(f"Successfully moving {p.get('full_name')} who is flagged sell")

    if delta >= 10:
        positives.append(f"Strong value return: +{delta} pts in your favor")

    verdict = (
        f"{win_margin.title()} for you (score: {receiving_total} received vs {giving_total} given, "
        f"delta: {'+' if delta >= 0 else ''}{delta})"
    )

    return TradeAnalysis(
        giving_players=[{"name": p.get("full_name"), "score": p["score"],
                          "position": p.get("position"), "age": p.get("age"),
                          "grade": p.get("dynasty_grade")} for p in giving_data],
        receiving_players=[{"name": p.get("full_name"), "score": p["score"],
                             "position": p.get("position"), "age": p.get("age"),
                             "grade": p.get("dynasty_grade")} for p in receiving_data],
        giving_picks=giving_pick_data,
        receiving_picks=receiving_pick_data,
        giving_total=giving_total,
        receiving_total=receiving_total,
        delta=delta,
        win_margin=win_margin,
        positional_impact=pos_impact,
        verdict=verdict,
        concerns=concerns,
        positives=positives,
    )


# ---------------------------------------------------------------------------
# Roster analysis summary
# ---------------------------------------------------------------------------

@dataclass
class RosterAnalysis:
    total_players: int
    by_position: dict[str, int]
    positional_needs: list[PositionalNeeds]
    age_buckets: dict[str, int]  # "young (<24)" | "prime (24-28)" | "vet (29+)"
    grade_distribution: dict[str, int]
    sell_flags: list[str]
    buy_flags: list[str]
    cut_candidates: list[str]
    avg_dynasty_score: float
    is_superflex: bool


def get_roster_analysis(
    conn: sqlite3.Connection,
    is_superflex: bool = False,
    roster_positions: Optional[list[str]] = None,
) -> RosterAnalysis:
    rows = conn.execute(
        """
        SELECT
            r.player_id, r.roster_slot,
            p.full_name, p.position, p.team, p.age, p.years_exp,
            pn.dynasty_grade, pn.action_flag, pn.trade_value
        FROM roster r
        LEFT JOIN players p ON r.player_id = p.player_id
        LEFT JOIN player_notes pn ON r.player_id = pn.player_id
        """
    ).fetchall()

    by_pos: dict[str, int] = {}
    age_buckets: dict[str, int] = {"young (<24)": 0, "prime (24-28)": 0, "vet (29+)": 0, "unknown": 0}
    grade_dist: dict[str, int] = {"S": 0, "A": 0, "B": 0, "C": 0, "D": 0, "ungraded": 0}
    sell_flags: list[str] = []
    buy_flags: list[str] = []
    cut_candidates: list[str] = []
    scores: list[int] = []

    for row in rows:
        pos = (row["position"] or "?").upper()
        by_pos[pos] = by_pos.get(pos, 0) + 1

        age = row["age"]
        if age is None:
            age_buckets["unknown"] += 1
        elif age < 24:
            age_buckets["young (<24)"] += 1
        elif age <= 28:
            age_buckets["prime (24-28)"] += 1
        else:
            age_buckets["vet (29+)"] += 1

        grade = row["dynasty_grade"]
        if grade and grade.upper() in grade_dist:
            grade_dist[grade.upper()] += 1
        else:
            grade_dist["ungraded"] += 1

        flag = row["action_flag"]
        name = row["full_name"] or row["player_id"]
        if flag == "sell":
            sell_flags.append(name)
        elif flag == "buy":
            buy_flags.append(name)
        elif flag == "cut":
            cut_candidates.append(name)

        score = compute_player_score(
            pos, age, row["years_exp"], grade, row["trade_value"], is_superflex
        )
        scores.append(score)

    needs = analyze_positional_needs(conn, roster_positions or [], is_superflex)
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0

    return RosterAnalysis(
        total_players=len(rows),
        by_position=by_pos,
        positional_needs=needs,
        age_buckets=age_buckets,
        grade_distribution=grade_dist,
        sell_flags=sell_flags,
        buy_flags=buy_flags,
        cut_candidates=cut_candidates,
        avg_dynasty_score=avg_score,
        is_superflex=is_superflex,
    )
