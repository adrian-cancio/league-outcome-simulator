"""Data access helpers for SofaScore and local replay fixtures."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from curl_cffi import requests

from .utils import (
    TEAM_ROW_HEADER,
    apply_result_to_split_tables,
    build_table_from_team_stats,
    stable_seed_from_text,
    team_stats_from_table,
)


LOGGER = logging.getLogger(__name__)

BrowserImpersonation = Literal[
    "chrome99",
    "chrome100",
    "chrome101",
    "chrome104",
    "chrome107",
    "chrome110",
    "chrome116",
    "chrome119",
    "chrome120",
    "chrome123",
    "chrome124",
    "chrome131",
    "edge99",
    "edge101",
]


DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "User-Agent": "Mozilla/5.0",
}


@dataclass(slots=True)
class ReplaySnapshot:
    """A replayable dataset for simulation or backtesting."""

    tournament_id: int
    season_id: int
    league_name: str
    snapshot_label: str
    base_table: list[list]
    home_table: list[list]
    away_table: list[list]
    fixtures: list[dict[str, Any]]
    team_colors: dict[str, dict[str, str | None]]
    metadata: dict[str, Any]


class SofaScoreClient:
    """HTTP client for fetching football data from SofaScore."""

    BASE_URL = "https://api.sofascore.com/api/v1"

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        use_disk_cache: bool = True,
        force_refresh: bool = False,
        timeout: int = 30,
        retries: int = 3,
        retry_delay: float = 1.0,
        impersonate: BrowserImpersonation = "chrome124",
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay
        self.impersonate = cast(BrowserImpersonation, impersonate)
        self.json_cache: dict[str, Any] = {}
        self.cache_dir = cache_dir or Path(".cache") / "sofascore"
        self.use_disk_cache = use_disk_cache
        self.force_refresh = force_refresh
        self.session = requests.Session(headers=DEFAULT_HEADERS)

    def _cache_path(self, url: str) -> Path:
        filename = f"{stable_seed_from_text(url):010d}.json"
        return self.cache_dir / filename

    def fetch_json(self, url: str, *, force_refresh: bool = False) -> Any:
        """Fetch JSON data from a URL with cache and retries."""
        force_refresh = force_refresh or self.force_refresh
        if not force_refresh and url in self.json_cache:
            LOGGER.debug("Cache hit for %s", url)
            return self.json_cache[url]

        cache_path = self._cache_path(url)
        if self.use_disk_cache and cache_path.exists() and not force_refresh:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            self.json_cache[url] = payload
            LOGGER.debug("Disk cache hit for %s", url)
            return payload

        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.get(
                    url,
                    impersonate=cast(BrowserImpersonation, self.impersonate),
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
                self.json_cache[url] = payload
                if self.use_disk_cache:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps(payload), encoding="utf-8")
                return payload
            except (
                Exception
            ) as exc:  # pragma: no cover - exercised in integration style
                last_error = exc
                LOGGER.warning(
                    "Failed to fetch %s on attempt %s/%s: %s",
                    url,
                    attempt,
                    self.retries,
                    exc,
                )
                if attempt < self.retries:
                    time.sleep(self.retry_delay * attempt)

        raise RuntimeError(f"Could not fetch SofaScore URL: {url}") from last_error

    def get_seasons(self, tournament_id: int) -> list[dict[str, Any]]:
        """Return all seasons for a tournament."""
        url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/seasons"
        data = self.fetch_json(url)
        return data.get("seasons", [])

    def get_current_season_id(self, tournament_id: int) -> int | None:
        """Best-effort detection of the active season."""
        seasons = self.get_seasons(tournament_id)
        if not seasons:
            return None

        now = datetime.now(UTC)
        year_candidates = {
            str(now.year),
            str(now.year - 1),
            f"{(now.year - 1) % 100:02d}/{now.year % 100:02d}",
            f"{now.year % 100:02d}/{(now.year + 1) % 100:02d}",
        }

        scored = []
        for season in seasons:
            score = 0
            year = str(season.get("year", ""))
            if year in year_candidates:
                score += 10
            name = str(season.get("name", "")).lower()
            if str(now.year) in name or str(now.year - 1) in name:
                score += 3
            score += season.get("id", 0) / 1_000_000_000
            scored.append((score, season))

        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1].get("id") if scored else None

    def resolve_season_id(
        self,
        tournament_id: int,
        *,
        season_id: int | None = None,
        season_year: str | None = None,
    ) -> int | None:
        """Resolve a season either by id, explicit year string or current season."""
        if season_id is not None:
            return season_id
        seasons = self.get_seasons(tournament_id)
        if season_year:
            for season in seasons:
                if (
                    str(season.get("year")) == season_year
                    or str(season.get("name")) == season_year
                ):
                    return season.get("id")
            raise ValueError(
                f"Season '{season_year}' not found for tournament {tournament_id}"
            )
        return self.get_current_season_id(tournament_id)

    def _build_table(self, rows: list[dict[str, Any]]) -> list[list]:
        table_rows = [TEAM_ROW_HEADER.copy()]
        for row in rows:
            table_rows.append(
                [
                    row["team"]["name"],
                    row.get("matches", 0),
                    row.get("wins", 0),
                    row.get("draws", 0),
                    row.get("losses", 0),
                    row.get("scoresFor", 0),
                    row.get("scoresAgainst", 0),
                    row.get("points", 0),
                ]
            )
        return table_rows

    def get_standings(
        self, tournament_id: int, season_id: int, scope: str
    ) -> list[list] | None:
        """Get standings for a given scope."""
        url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/standings/{scope}"
        data = self.fetch_json(url)
        standings = data.get("standings", [])
        if not standings:
            return None

        # Some competitions expose multiple groups here (e.g. MLS conferences plus an
        # overall table). Prefer the aggregate table when present, otherwise fall back to
        # the largest standings block instead of blindly taking the first one.
        selected_standing = max(
            standings,
            key=lambda standing: (
                len(standing.get("rows", [])),
                "conference" not in str(standing.get("name", "")).lower(),
            ),
        )
        rows = selected_standing.get("rows", [])
        return self._build_table(rows)

    def get_league_table(self, tournament_id: int, season_id: int) -> list[list] | None:
        return self.get_standings(tournament_id, season_id, "total")

    def get_home_league_table(
        self, tournament_id: int, season_id: int
    ) -> list[list] | None:
        return self.get_standings(tournament_id, season_id, "home")

    def get_away_league_table(
        self, tournament_id: int, season_id: int
    ) -> list[list] | None:
        return self.get_standings(tournament_id, season_id, "away")

    def get_team_colors(
        self, tournament_id: int, season_id: int
    ) -> dict[str, dict[str, str | None]]:
        """Extract team colors from standings data."""
        url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/standings/total"
        data = self.fetch_json(url)
        team_colors: dict[str, dict[str, str | None]] = {}
        default_blue = "#374df5"
        for standing in data.get("standings", []):
            for row in standing.get("rows", []):
                team = row.get("team", {})
                name = team.get("name")
                if not name:
                    continue
                team_colors_data = team.get("teamColors", {})
                primary = team_colors_data.get("primary")
                secondary = team_colors_data.get("secondary")
                team_colors[name] = {
                    "primary": primary if primary and primary != default_blue else None,
                    "secondary": secondary
                    if secondary and secondary != default_blue
                    else None,
                }
        return team_colors

    def _event_to_fixture(self, event: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": event["id"],
            "h": {"title": event["homeTeam"]["name"]},
            "a": {"title": event["awayTeam"]["name"]},
            "datetime": event.get("startTimestamp"),
            "status": event.get("status", {}).get("type"),
        }

    def get_events_page(
        self,
        tournament_id: int,
        season_id: int,
        direction: str,
        page: int,
    ) -> dict[str, Any]:
        """Return a page of events in a given direction."""
        url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/events/{direction}/{page}"
        return self.fetch_json(url)

    def _collect_events(
        self,
        tournament_id: int,
        season_id: int,
        direction: str,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        page = 0
        while True:
            try:
                data = self.get_events_page(tournament_id, season_id, direction, page)
            except RuntimeError:
                if page == 0:
                    return []
                raise
            page_events = data.get("events", [])
            if not page_events:
                break
            events.extend(page_events)
            if not data.get("hasNextPage"):
                break
            page += 1
        return events

    def get_remaining_fixtures(
        self, tournament_id: int, season_id: int
    ) -> list[dict[str, Any]]:
        """Return not-started fixtures for a season."""
        events = self._collect_events(tournament_id, season_id, "next")
        fixtures = [
            self._event_to_fixture(event)
            for event in events
            if event.get("status", {}).get("type") == "notstarted"
        ]
        LOGGER.info("Found %s remaining fixtures", len(fixtures))
        return fixtures

    def get_completed_events(
        self, tournament_id: int, season_id: int
    ) -> list[dict[str, Any]]:
        """Return finished events for a season."""
        events = self._collect_events(tournament_id, season_id, "last")
        completed = [
            event
            for event in events
            if event.get("status", {}).get("type") == "finished"
        ]
        completed.sort(key=lambda event: event.get("startTimestamp", 0))
        return completed

    def get_season_metadata(self, tournament_id: int, season_id: int) -> dict[str, Any]:
        """Fetch a small metadata snapshot for a season."""
        seasons = self.get_seasons(tournament_id)
        season = next(
            (season for season in seasons if season.get("id") == season_id), None
        )
        league_name = season.get("name") if season else f"Tournament {tournament_id}"
        return {
            "tournament_id": tournament_id,
            "season_id": season_id,
            "season": season,
            "league_name": league_name,
        }

    def build_snapshot_from_cutoff(
        self,
        tournament_id: int,
        season_id: int,
        *,
        matchday_cutoff: int | None = None,
        completed_match_count: int | None = None,
    ) -> ReplaySnapshot:
        """Build standings and remaining fixtures from completed historical events."""
        metadata = self.get_season_metadata(tournament_id, season_id)
        all_completed = self.get_completed_events(tournament_id, season_id)
        all_upcoming = self.get_remaining_fixtures(tournament_id, season_id)

        if matchday_cutoff is None and completed_match_count is None:
            raise ValueError(
                "Backtest snapshot requires matchday_cutoff or completed_match_count"
            )

        if matchday_cutoff is not None:
            completed_subset = [
                event
                for event in all_completed
                if event.get("roundInfo", {}).get("round") <= matchday_cutoff
            ]
            future_finished = [
                event
                for event in all_completed
                if event.get("roundInfo", {}).get("round") > matchday_cutoff
            ]
            label = f"matchday-{matchday_cutoff}"
        else:
            completed_subset = all_completed[:completed_match_count]
            future_finished = all_completed[completed_match_count:]
            label = f"after-{completed_match_count}-matches"

        season_total = self.get_league_table(tournament_id, season_id)
        if not season_total:
            raise RuntimeError("Could not load standings to infer teams")

        team_stats = team_stats_from_table(
            [
                TEAM_ROW_HEADER.copy(),
                *[[row[0], 0, 0, 0, 0, 0, 0, 0] for row in season_total[1:]],
            ]
        )
        home_stats = team_stats_from_table(
            [
                TEAM_ROW_HEADER.copy(),
                *[[row[0], 0, 0, 0, 0, 0, 0, 0] for row in season_total[1:]],
            ]
        )
        away_stats = team_stats_from_table(
            [
                TEAM_ROW_HEADER.copy(),
                *[[row[0], 0, 0, 0, 0, 0, 0, 0] for row in season_total[1:]],
            ]
        )

        for event in completed_subset:
            home_team = event["homeTeam"]["name"]
            away_team = event["awayTeam"]["name"]
            home_goals = int(event.get("homeScore", {}).get("current", 0))
            away_goals = int(event.get("awayScore", {}).get("current", 0))

            apply_result_to_split_tables(
                team_stats,
                home_stats,
                away_stats,
                home_team,
                away_team,
                home_goals,
                away_goals,
            )

        future_fixtures = [
            self._event_to_fixture(event) for event in future_finished
        ] + all_upcoming
        future_fixtures.sort(key=lambda fixture: fixture.get("datetime") or 0)

        team_colors = self.get_team_colors(tournament_id, season_id)
        return ReplaySnapshot(
            tournament_id=tournament_id,
            season_id=season_id,
            league_name=metadata["league_name"],
            snapshot_label=label,
            base_table=build_table_from_team_stats(team_stats),
            home_table=build_table_from_team_stats(home_stats),
            away_table=build_table_from_team_stats(away_stats),
            fixtures=future_fixtures,
            team_colors=team_colors,
            metadata={
                **metadata,
                "snapshot_label": label,
                "completed_events_used": len(completed_subset),
                "remaining_fixtures": len(future_fixtures),
                "cutoff_matchday": matchday_cutoff,
                "cutoff_completed_matches": completed_match_count,
            },
        )

    def export_snapshot(self, snapshot: ReplaySnapshot, destination: Path) -> Path:
        """Persist a replay snapshot to disk."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tournament_id": snapshot.tournament_id,
            "season_id": snapshot.season_id,
            "league_name": snapshot.league_name,
            "snapshot_label": snapshot.snapshot_label,
            "base_table": snapshot.base_table,
            "home_table": snapshot.home_table,
            "away_table": snapshot.away_table,
            "fixtures": snapshot.fixtures,
            "team_colors": snapshot.team_colors,
            "metadata": snapshot.metadata,
        }
        destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return destination

    def load_snapshot(self, snapshot_path: Path) -> ReplaySnapshot:
        """Load a replay snapshot from disk."""
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        return ReplaySnapshot(
            tournament_id=payload["tournament_id"],
            season_id=payload["season_id"],
            league_name=payload["league_name"],
            snapshot_label=payload["snapshot_label"],
            base_table=payload["base_table"],
            home_table=payload["home_table"],
            away_table=payload["away_table"],
            fixtures=payload["fixtures"],
            team_colors=payload["team_colors"],
            metadata=payload["metadata"],
        )

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self.session.close()
