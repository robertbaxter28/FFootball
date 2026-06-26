"""
Sleeper REST API client.

All endpoints are public and require no authentication.
Rate limits: Sleeper asks for reasonable use; no official documented limit.
"""
import json
import time
import urllib.error
import urllib.request
from typing import Any

BASE_URL = "https://api.sleeper.app/v1"
_DEFAULT_TIMEOUT = 30


class SleeperAPIError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        self.status = status
        self.url = url
        super().__init__(f"Sleeper API {status} for {url}: {body[:200]}")


def _get(path: str, timeout: int = _DEFAULT_TIMEOUT) -> Any:
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "ffootball-agent/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise SleeperAPIError(e.code, url, body) from e
    except urllib.error.URLError as e:
        raise SleeperAPIError(0, url, str(e.reason)) from e


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

def get_user(username: str) -> dict:
    """Fetch user by username. Returns user object with user_id."""
    return _get(f"/user/{username}")


def get_user_leagues(user_id: str, season: str) -> list[dict]:
    """All NFL leagues for a user in a given season."""
    result = _get(f"/user/{user_id}/leagues/nfl/{season}")
    return result if result else []


# ---------------------------------------------------------------------------
# League
# ---------------------------------------------------------------------------

def get_league(league_id: str) -> dict:
    """Full league settings object."""
    return _get(f"/league/{league_id}")


def get_rosters(league_id: str) -> list[dict]:
    """All rosters in the league (players, picks, owner_id, roster_id)."""
    result = _get(f"/league/{league_id}/rosters")
    return result if result else []


def get_users(league_id: str) -> list[dict]:
    """All users (managers) in the league."""
    result = _get(f"/league/{league_id}/users")
    return result if result else []


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------

def get_all_players() -> dict[str, dict]:
    """
    Full NFL player universe as {player_id: player_object}.

    This is a large response (~4MB). Cache results — Sleeper updates this
    no more than once per day and asks callers to do the same.
    """
    return _get("/players/nfl")


def get_trending_players(
    sport: str = "nfl",
    trend_type: str = "add",
    lookback_hours: int = 24,
    limit: int = 25,
) -> list[dict]:
    """Trending adds or drops across all Sleeper leagues."""
    result = _get(
        f"/players/{sport}/trending/{trend_type}"
        f"?lookback_hours={lookback_hours}&limit={limit}"
    )
    return result if result else []


# ---------------------------------------------------------------------------
# In-season
# ---------------------------------------------------------------------------

def get_matchups(league_id: str, week: int) -> list[dict]:
    """Weekly matchup data for all rosters. Returns list of matchup objects."""
    result = _get(f"/league/{league_id}/matchups/{week}")
    return result if result else []


def get_transactions(league_id: str, week: int) -> list[dict]:
    """
    All transactions (trades, waivers, free agent adds) for a given week.
    Week 0 returns pre-season transactions.
    """
    result = _get(f"/league/{league_id}/transactions/{week}")
    return result if result else []


def get_transactions_range(league_id: str, weeks: int = 4) -> list[dict]:
    """
    Fetch transactions for the last `weeks` weeks, deduped by transaction_id.
    Tries weeks from current NFL week backwards; stops at week 1.
    """
    from datetime import datetime, timezone

    # Rough current-week estimate: season starts week 1 around early Sept
    # Use a simple heuristic; the sync engine can pass an explicit week later
    now = datetime.now(timezone.utc)
    # Approximate: Sept 4 is typically week 1 kickoff
    year = now.year
    week_start = datetime(year, 9, 4, tzinfo=timezone.utc)
    current_week = max(1, min(18, int((now - week_start).days / 7) + 1))

    seen: set[str] = set()
    all_txns: list[dict] = []
    for w in range(current_week, max(0, current_week - weeks) - 1, -1):
        try:
            txns = get_transactions(league_id, w)
        except SleeperAPIError:
            continue
        for t in txns:
            tid = t.get("transaction_id") or t.get("leg", "") + str(t.get("created", ""))
            if tid not in seen:
                seen.add(tid)
                all_txns.append(t)

    return all_txns


def get_traded_picks(league_id: str) -> list[dict]:
    """All future draft picks that have been traded in this league."""
    result = _get(f"/league/{league_id}/traded_picks")
    return result if result else []


def get_draft_picks(league_id: str) -> list[dict]:
    """All upcoming/past drafts for this league."""
    result = _get(f"/league/{league_id}/drafts")
    return result if result else []


# ---------------------------------------------------------------------------
# NFL State (current week/season info)
# ---------------------------------------------------------------------------

def get_nfl_state() -> dict:
    """
    Current NFL state: season, week, season_type (pre/regular/post), etc.
    Use this instead of estimating the current week.
    """
    return _get("/state/nfl")
