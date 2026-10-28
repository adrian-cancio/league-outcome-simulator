"""Regression tests for the read-only review findings (data layer and live CLI paths)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from league_outcome_simulator import cli, data
from league_outcome_simulator.data import (
    MAX_EVENT_PAGES,
    STATUS_FINISHED,
    STATUS_IN_PROGRESS,
    STATUS_NOT_STARTED,
    SeasonNotFoundError,
    SeasonSelectionError,
    SofaScoreClient,
    SofaScoreError,
    iso_utc,
)
from tests.support import (
    DAY,
    NOW,
    FakeResponse,
    FakeSession,
    ScriptedSession,
    event,
    install_session,
    make_routes,
    season_routes,
)


ClientFactory = Callable[..., SofaScoreClient]
BASE = "/unique-tournament/54/season"


def _seasons(*pairs: tuple[int, str]) -> list[dict[str, Any]]:
    return [
        {"name": f"Test League {year}", "year": year, "id": season_id}
        for season_id, year in pairs
    ]


def _empty_standings() -> dict[str, Any]:
    return {"standings": [{"name": "Total", "rows": []}]}


def _preseason_routes(*, standings: str) -> dict[str, Any]:
    """A 27/28 season with 6 scheduled fixtures and one of three 'no table' shapes."""
    routes = make_routes(
        _seasons((300, "27/28")), season_routes(54, 300, played=0, upcoming=6)
    )
    for scope in ("total", "home", "away"):
        path = f"{BASE}/300/standings/{scope}"
        if standings == "404":
            del routes[path]
        elif standings == "empty-rows":
            routes[path] = _empty_standings()
        elif standings == "empty-list":
            routes[path] = {"standings": []}
        else:  # pragma: no cover - guards against typos in the tests
            raise AssertionError(standings)
    return routes


@pytest.fixture
def mocked_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Route SofaScoreClient's HTTP session to a FakeSession; isolate the cache dir."""
    monkeypatch.chdir(tmp_path)
    state: dict[str, Any] = {"routes": {}}

    def factory(*_args: Any, **_kwargs: Any) -> FakeSession:
        return FakeSession(state["routes"])

    monkeypatch.setattr(data.requests, "Session", factory)
    return state


def _args(command: str, out: Path, *extra: str) -> list[str]:
    return [
        command,
        "la-liga-2",
        "--max-simulations",
        "200",
        "--batch-size",
        "100",
        "--plot",
        "off",
        "--no-gui",
        "--seed",
        "3",
        "--no-auto-build-rust",
        "--output-dir",
        str(out),
        *extra,
    ]


def _only_manifest(out: Path) -> dict[str, Any]:
    (path,) = out.glob("*/*/*/manifest.json")
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------- 1. pre-season without a usable table


@pytest.mark.parametrize("shape", ["404", "empty-rows", "empty-list"])
def test_preseason_without_standings_simulates_from_fixture_teams(
    mocked_network: dict[str, Any],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    shape: str,
) -> None:
    mocked_network["routes"] = _preseason_routes(standings=shape)
    with caplog.at_level(logging.WARNING):
        exit_code = cli.main(_args("simulate", tmp_path / "out"))
    assert exit_code == 0
    assert any("No standings published" in m for m in caplog.messages)

    manifest = _only_manifest(tmp_path / "out")
    assert manifest["season_id"] == 300
    assert manifest["snapshot_metadata"]["season_status"] == STATUS_NOT_STARTED
    teams = {entry["team"] for entry in manifest["position_probabilities"]}
    assert teams == {"Alpha FC", "Bravo United"}  # taken from the fixture list
    text = next((tmp_path / "out").glob("*/*/*/simulation.txt")).read_text(encoding="utf-8")
    assert "(0 played)" in text


def test_get_standings_returns_none_for_404_and_headerless_tables(
    make_client: ClientFactory,
) -> None:
    for shape in ("404", "empty-rows", "empty-list"):
        client = make_client(_preseason_routes(standings=shape), use_disk_cache=False)
        assert client.get_league_table(54, 300) is None
        assert client.get_home_league_table(54, 300) is None
        assert client.get_away_league_table(54, 300) is None
        assert client.get_team_colors(54, 300) == {}


def test_in_progress_season_without_standings_fails_instead_of_zeroing_the_table(
    mocked_network: dict[str, Any], tmp_path: Path
) -> None:
    # Played matches exist (events/last) but the table is missing: the empty-table
    # shortcut is only for seasons that really have not started.
    routes = make_routes(
        _seasons((200, "26/27")), season_routes(54, 200, played=5, upcoming=3)
    )
    for scope in ("total", "home", "away"):
        del routes[f"{BASE}/200/standings/{scope}"]
    mocked_network["routes"] = routes
    with pytest.raises(SystemExit) as excinfo:
        cli.main(_args("simulate", tmp_path / "out"))
    assert "standings" in str(excinfo.value)
    assert not (tmp_path / "out").exists()


# -------------------------------------------- 2. live / postponed matches in the last round


def _last_round_routes(pending_status: str, *, start: float) -> dict[str, Any]:
    routes = make_routes(
        _seasons((200, "26/27")),
        season_routes(54, 200, played=37, upcoming=0, last_start=NOW - 3600),
    )
    routes[f"{BASE}/200/events/next/0"] = {
        "events": [event(1, pending_status, start, "Charlie Town", "Delta City")],
        "hasNextPage": False,
    }
    return routes


@pytest.mark.parametrize(
    ("status", "start"),
    [("inprogress", NOW - 1800), ("postponed", NOW + 5 * DAY), ("interrupted", NOW - 600)],
)
def test_pending_match_keeps_the_season_in_progress(
    make_client: ClientFactory, status: str, start: float
) -> None:
    client = make_client(_last_round_routes(status, start=start))
    selection = client.select_season(54)
    assert selection.season_id == 200
    assert selection.status == STATUS_IN_PROGRESS
    assert selection.state.pending == 1
    assert selection.state.has_fixtures is False  # nothing schedulable to simulate


def test_long_dead_postponed_match_does_not_block_a_finished_season(
    make_client: ClientFactory,
) -> None:
    routes = _last_round_routes("postponed", start=NOW - 60 * DAY)
    client = make_client(routes)
    assert client.season_state(54, 200).status == STATUS_FINISHED


def test_live_match_is_neither_simulated_nor_given_a_score(
    mocked_network: dict[str, Any], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One live game plus one scheduled game: only the scheduled one is simulated."""
    routes = _last_round_routes("inprogress", start=NOW - 1800)
    routes[f"{BASE}/200/events/next/0"]["events"].append(
        event(2, "notstarted", NOW + DAY, "Alpha FC", "Bravo United")
    )
    mocked_network["routes"] = routes

    with caplog.at_level(logging.WARNING):
        assert cli.main(_args("simulate", tmp_path / "out")) == 0
    assert any("live, postponed or interrupted" in m for m in caplog.messages)

    manifest = _only_manifest(tmp_path / "out")
    meta = manifest["snapshot_metadata"]
    assert meta["season_pending_matches"] == 1
    # The live Charlie Town v Delta City game gave nobody points: both tables are
    # exactly as published (37 played each), not advanced by an invented result.
    text = next((tmp_path / "out").glob("*/*/*/simulation.txt")).read_text(encoding="utf-8")
    assert "Charlie Town - 37 pts (37 played)" in text
    assert "Delta City - 0 pts (37 played)" in text
    assert "matches per iteration: 1)" in text  # only the scheduled match


def test_only_pending_matches_left_gives_a_precise_message(
    mocked_network: dict[str, Any], tmp_path: Path
) -> None:
    mocked_network["routes"] = _last_round_routes("inprogress", start=NOW - 1800)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(_args("simulate", tmp_path / "out"))
    message = str(excinfo.value)
    assert "No fixtures left" in message
    assert "live, postponed or interrupted" in message
    assert not (tmp_path / "out").exists()


# ---------------------------------------------------- 3. backtest default season


def _finished_seasons_routes() -> dict[str, Any]:
    return make_routes(
        _seasons((200, "26/27"), (100, "25/26")),
        season_routes(54, 200, played=38, upcoming=0),
        season_routes(54, 100, played=38, upcoming=0),
    )


def test_backtest_default_season_is_the_newest_with_played_matches(
    make_client: ClientFactory,
) -> None:
    selection = make_client(_finished_seasons_routes()).select_backtest_season(54)
    assert selection.season_id == 200
    assert selection.status == STATUS_FINISHED
    assert selection.warnings == []


def test_backtest_default_skips_a_season_that_has_not_started(
    make_client: ClientFactory,
) -> None:
    routes = make_routes(
        _seasons((300, "27/28"), (200, "26/27")),
        season_routes(54, 300, played=0, upcoming=10),
        season_routes(54, 200, played=38, upcoming=0),
    )
    assert make_client(routes).select_backtest_season(54).season_id == 200


def test_backtest_default_warns_when_only_an_in_progress_season_exists(
    make_client: ClientFactory,
) -> None:
    routes = make_routes(
        _seasons((200, "26/27")), season_routes(54, 200, played=5, upcoming=3)
    )
    selection = make_client(routes).select_backtest_season(54)
    assert selection.season_id == 200
    assert any("still in progress" in warning for warning in selection.warnings)


def test_backtest_default_refuses_when_nothing_has_been_played(
    make_client: ClientFactory,
) -> None:
    routes = make_routes(
        _seasons((300, "27/28")), season_routes(54, 300, played=0, upcoming=10)
    )
    with pytest.raises(SeasonSelectionError, match="played matches"):
        make_client(routes).select_backtest_season(54)


def test_prediction_selection_still_refuses_a_finished_season(
    make_client: ClientFactory,
) -> None:
    # The backtest fix must not relax the prediction rule.
    with pytest.raises(SeasonSelectionError):
        make_client(_finished_seasons_routes()).select_season(54)


def test_backtest_cli_without_season_flags_runs_on_the_finished_season(
    mocked_network: dict[str, Any], tmp_path: Path
) -> None:
    mocked_network["routes"] = _finished_seasons_routes()
    exit_code = cli.main(
        [*_args("backtest", tmp_path / "out", "--matchday-cutoff", "0", "--export-snapshot")]
    )
    assert exit_code == 0
    manifest = _only_manifest(tmp_path / "out")
    assert manifest["season_id"] == 200
    assert manifest["data_source"] == "network"
    (run_dir,) = {p.parent for p in (tmp_path / "out").glob("*/*/*/manifest.json")}
    assert (run_dir / "backtest.json").is_file()
    snapshots = list((tmp_path / "out" / "snapshots").glob("*/*.json"))
    assert snapshots
    exported = json.loads(snapshots[0].read_text(encoding="utf-8"))
    assert exported["season_id"] == 200
    assert exported["metadata"]["data_source"] == "network"


# ------------------------------------------------ 4. provenance scoped to the season used


def test_summary_ignores_seasons_that_were_only_inspected(
    make_client: ClientFactory,
) -> None:
    routes = make_routes(
        _seasons((300, "27/28"), (200, "26/27")),
        season_routes(54, 300, played=0, upcoming=6),
        season_routes(54, 200, played=5, upcoming=3),
    )
    client = make_client(routes)
    assert client.select_season(54).season_id == 200
    client.get_league_table(54, 200)

    scoped = client.data_summary(54, 200)
    assert scoped["network_requests"] == 2  # events/next/0 + standings/total
    assert client.data_summary()["network_requests"] == 4  # old, unscoped behaviour


def test_summary_age_and_source_come_only_from_the_selected_season(
    make_client: ClientFactory, clock: Any
) -> None:
    routes = make_routes(
        _seasons((300, "27/28"), (200, "26/27")),
        season_routes(54, 300, played=0, upcoming=6),
        season_routes(54, 200, played=5, upcoming=3),
    )
    make_client(routes).get_league_table(54, 300)  # old cache entry of another season
    clock.advance(600)

    client = make_client(routes)
    client.get_league_table(54, 300)  # served from cache, 10 minutes old
    client.get_league_table(54, 200)  # fresh from the network
    scoped = client.data_summary(54, 200)
    assert scoped["data_source"] == "network"
    assert scoped["fetched_at"] == iso_utc(NOW + 600)
    assert client.data_summary()["data_source"] == "mixed"


def test_live_run_manifest_ignores_the_skipped_newer_season(
    mocked_network: dict[str, Any], tmp_path: Path
) -> None:
    mocked_network["routes"] = make_routes(
        _seasons((300, "27/28"), (200, "26/27")),
        season_routes(54, 300, played=0, upcoming=6),
        season_routes(54, 200, played=5, upcoming=3),
    )
    assert cli.main(_args("simulate", tmp_path / "out")) == 0
    meta = _only_manifest(tmp_path / "out")["snapshot_metadata"]
    assert meta["season_id"] == 200
    # Season 200's own resources only: events/next/0 + standings total/home/away.
    # Season 300's probes (skipped candidate) must not be counted.
    assert meta["network_requests"] == 4
    assert meta["data_source"] == "network"


def test_provenance_without_any_timestamp() -> None:
    snapshot = data.ReplaySnapshot(
        tournament_id=1,
        season_id=7,
        league_name="X",
        snapshot_label="s",
        base_table=[],
        home_table=[],
        away_table=[],
        fixtures=[],
        team_colors={},
        metadata={},
    )
    info = cli.data_provenance(snapshot)
    assert info["fetched_at"] is None
    assert info["data_age_seconds"] is None
    assert info["data_source"] == "snapshot"
    lines = cli.provenance_lines(info)
    assert lines[0] == "Season: unknown season (id 7)"
    assert lines[1] == "Data source: snapshot, fetched at unknown"


# ------------------------------------------------------- 5. invalid explicit season id


def test_unknown_season_id_is_rejected(make_client: ClientFactory) -> None:
    routes = make_routes(
        _seasons((200, "26/27")), season_routes(54, 200, played=5, upcoming=3)
    )
    client = make_client(routes)
    with pytest.raises(SeasonNotFoundError) as excinfo:
        client.select_season(54, season_id=999)
    assert isinstance(excinfo.value, ValueError)  # backward compatible
    assert "999" in str(excinfo.value) and "26/27" in str(excinfo.value)
    with pytest.raises(SeasonNotFoundError):
        client.resolve_season_id(54, season_id=999)
    with pytest.raises(SeasonNotFoundError):
        client.select_backtest_season(54, season_id=999)


@pytest.mark.parametrize("command", ["simulate", "backtest"])
def test_cli_unknown_season_id_is_a_clean_error(
    mocked_network: dict[str, Any], tmp_path: Path, command: str
) -> None:
    mocked_network["routes"] = make_routes(
        _seasons((200, "26/27")), season_routes(54, 200, played=5, upcoming=3)
    )
    extra = ["--matchday-cutoff", "1"] if command == "backtest" else []
    with pytest.raises(SystemExit) as excinfo:
        cli.main(_args(command, tmp_path / "out", "--season-id", "999", *extra))
    assert "999" in str(excinfo.value) and "not listed" in str(excinfo.value)
    assert not (tmp_path / "out").exists()


def test_listed_explicit_season_id_still_works(make_client: ClientFactory) -> None:
    routes = make_routes(
        _seasons((200, "26/27"), (100, "25/26")),
        season_routes(54, 200, played=5, upcoming=3),
        season_routes(54, 100, played=38, upcoming=0),
    )
    assert make_client(routes).select_season(54, season_id=100).status == STATUS_FINISHED


# ------------------------------------------------------ 6. backtest with a bad season year


def test_backtest_bad_season_year_is_a_clean_error(
    mocked_network: dict[str, Any], tmp_path: Path
) -> None:
    mocked_network["routes"] = _finished_seasons_routes()
    with pytest.raises(SystemExit) as excinfo:  # not a raw ValueError traceback
        cli.main(
            _args("backtest", tmp_path / "out", "--season-year", "99/99", "--matchday-cutoff", "1")
        )
    message = str(excinfo.value)
    assert "99/99" in message and "not found" in message
    assert "25/26" in message  # lists what is available


# ---------------------------------------- low-priority gaps: retries, 404s, pagination


def test_server_errors_are_retried_then_reported(make_client: ClientFactory) -> None:
    client = make_client({}, retries=3, use_disk_cache=False)
    session = install_session(client, ScriptedSession(lambda: FakeResponse(503, {})))
    with pytest.raises(SofaScoreError, match="Could not fetch"):
        client.get_seasons(54)
    assert len(session.calls) == 3


def test_network_exceptions_are_retried(make_client: ClientFactory) -> None:
    attempts = {"n": 0}

    def flaky() -> FakeResponse:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("boom")
        return FakeResponse(200, {"seasons": []})

    client = make_client({}, retries=3, use_disk_cache=False)
    install_session(client, ScriptedSession(flaky))
    assert client.get_seasons(54) == []
    assert attempts["n"] == 3


def test_404_is_final_and_does_not_try_the_alternative_host(
    make_client: ClientFactory,
) -> None:
    client = make_client({}, use_disk_cache=False)
    session = install_session(client, FakeSession({}))
    with pytest.raises(data.SofaScoreNotFoundError):
        client.get_seasons(54)
    assert len(session.calls) == 1


def test_event_pagination_stops_at_the_guard(make_client: ClientFactory) -> None:
    # An API that claims a next page forever must not hang the run.
    routes: dict[str, Any] = {
        f"{BASE}/200/events/next/{page}": {
            "events": [event(page, "notstarted", NOW + page)],
            "hasNextPage": True,
        }
        for page in range(MAX_EVENT_PAGES + 5)
    }
    client = make_client(routes, use_disk_cache=False)
    assert len(client._collect_events(54, 200, "next")) == MAX_EVENT_PAGES


def test_event_pagination_collects_every_page(make_client: ClientFactory) -> None:
    routes: dict[str, Any] = {}
    for page, has_next in ((0, True), (1, True), (2, False)):
        routes[f"{BASE}/200/events/next/{page}"] = {
            "events": [event(page, "notstarted", NOW + page)],
            "hasNextPage": has_next,
        }
    client = make_client(routes, use_disk_cache=False)
    assert [e["id"] for e in client._collect_events(54, 200, "next")] == [0, 1, 2]
