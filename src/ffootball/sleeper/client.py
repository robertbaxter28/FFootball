"""Sleeper REST API client — Phase 2 implementation."""


class SleeperClient:
    BASE = "https://api.sleeper.app/v1"

    def __init__(self):
        import httpx
        self._http = httpx.Client(base_url=self.BASE, timeout=30)

    def close(self):
        self._http.close()

    def _get(self, path: str) -> dict | list:
        r = self._http.get(path)
        r.raise_for_status()
        return r.json()

    def get_user(self, username: str) -> dict:
        return self._get(f"/user/{username}")

    def get_user_leagues(self, user_id: str, season: str) -> list:
        return self._get(f"/user/{user_id}/leagues/nfl/{season}")

    def get_league(self, league_id: str) -> dict:
        return self._get(f"/league/{league_id}")

    def get_rosters(self, league_id: str) -> list:
        return self._get(f"/league/{league_id}/rosters")

    def get_users(self, league_id: str) -> list:
        return self._get(f"/league/{league_id}/users")

    def get_all_players(self) -> dict:
        return self._get("/players/nfl")

    def get_matchups(self, league_id: str, week: int) -> list:
        return self._get(f"/league/{league_id}/matchups/{week}")

    def get_transactions(self, league_id: str, week: int) -> list:
        return self._get(f"/league/{league_id}/transactions/{week}")

    def get_traded_picks(self, league_id: str) -> list:
        return self._get(f"/league/{league_id}/traded_picks")
