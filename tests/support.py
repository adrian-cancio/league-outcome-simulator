"""Shared test doubles and payload builders (imported as ``tests.support``)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


NOW = 1_800_000_000.0  # fixed "current time" used by the fake clock
DAY = 86_400.0


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = str(self._payload)

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:  # pragma: no cover - not used by the client
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Stands in for curl_cffi's Session; routes by URL path and impersonation."""

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes: dict[str, Any] = routes or {}
        # (host_prefix, impersonation) pairs that answer HTTP 403
        self.blocked: set[tuple[str, str]] = set()
        self.block_everything = False
        self.calls: list[tuple[str, str]] = []

    def get(self, url: str, *, impersonate: str, timeout: float = 0) -> FakeResponse:
        self.calls.append((url, impersonate))
        if self.block_everything or any(
            url.startswith(prefix) and impersonate == profile
            for prefix, profile in self.blocked
        ):
            return FakeResponse(403, {"error": {"code": 403, "reason": "challenge"}})
        path = url.split("/api/v1", 1)[1]
        if path not in self.routes:
            return FakeResponse(404, {"error": {"code": 404, "reason": "Not Found"}})
        return FakeResponse(200, self.routes[path])

    def close(self) -> None:
        pass


class ScriptedSession(FakeSession):
    """Answers every GET with whatever ``responder`` returns (or raises)."""

    def __init__(self, responder: Callable[[], Any]) -> None:
        super().__init__({})
        self._responder = responder

    def get(self, url: str, *, impersonate: str, timeout: float = 0) -> Any:
        self.calls.append((url, impersonate))
        return self._responder()


def install_session(client: Any, session: FakeSession) -> FakeSession:
    """Swap a client's HTTP session for a test double, keeping the double's type."""
    client.session = session
    return session


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


TEAMS = ("Alpha FC", "Bravo United", "Charlie Town", "Delta City")


def _row(
    name: str, played: int, wins: int, draws: int, losses: int, gf: int, ga: int
) -> dict[str, Any]:
    return {
        "team": {
            "name": name,
            "teamColors": {"primary": "#cc0000", "secondary": "#ffffff"},
        },
        "matches": played,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "scoresFor": gf,
        "scoresAgainst": ga,
        "points": wins * 3 + draws,
    }


def standings_payload(played: int) -> dict[str, Any]:
    """Four-team table where every team has played ``played`` matches."""
    if played == 0:
        rows = [_row(team, 0, 0, 0, 0, 0, 0) for team in TEAMS]
    else:
        rows = [
            _row("Alpha FC", played, played, 0, 0, played * 2, 0),
            _row("Bravo United", played, 0, played, 0, played, played),
            _row("Charlie Town", played, 0, played, 0, played, played),
            _row("Delta City", played, 0, 0, played, 0, played * 2),
        ]
    return {"standings": [{"name": "Total", "rows": rows}]}


def event(
    event_id: int,
    status: str,
    start: float,
    home: str = "Alpha FC",
    away: str = "Bravo United",
) -> dict[str, Any]:
    return {
        "id": event_id,
        "startTimestamp": int(start),
        "status": {"type": status},
        "homeTeam": {"name": home},
        "awayTeam": {"name": away},
        "roundInfo": {"round": 1},
    }


def season_routes(
    tournament: int,
    season_id: int,
    *,
    played: int,
    upcoming: int,
    last_start: float = NOW - 10 * DAY,
) -> dict[str, Any]:
    """Routes describing one season: standings, 'next' events and 'last' events."""
    base = f"/unique-tournament/{tournament}/season/{season_id}"
    routes: dict[str, Any] = {
        f"{base}/standings/total": standings_payload(played),
        f"{base}/standings/home": standings_payload(played),
        f"{base}/standings/away": standings_payload(played),
    }
    if upcoming:
        routes[f"{base}/events/next/0"] = {
            "events": [
                event(season_id * 100 + i, "notstarted", NOW + (i + 1) * DAY)
                for i in range(upcoming)
            ],
            "hasNextPage": False,
        }
    if played:
        routes[f"{base}/events/last/0"] = {
            "events": [event(season_id * 100 + 90, "finished", last_start)],
            "hasNextPage": False,
        }
    return routes


def make_routes(
    seasons: list[dict[str, Any]], *per_season: dict[str, Any]
) -> dict[str, Any]:
    routes: dict[str, Any] = {"/unique-tournament/54/seasons": {"seasons": seasons}}
    for extra in per_season:
        routes.update(extra)
    return routes
