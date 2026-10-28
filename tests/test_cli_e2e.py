"""End-to-end CLI runs: snapshot file -> Rust extension -> artifacts, and mocked network."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from tests.support import FakeSession, make_routes, season_routes

from league_outcome_simulator import cli, data


FIXTURE_SNAPSHOT = Path(__file__).parent / "fixtures" / "sample_snapshot.json"
TEAMS = ["Alpha FC", "Bravo United", "Charlie Town", "Delta City"]


def _simulate(
    tmp_path: Path, *extra: str, league: str = "sample-league"
) -> dict[str, Any]:
    exit_code = cli.main(
        [
            "simulate",
            league,
            "--max-simulations",
            "2000",
            "--batch-size",
            "500",
            "--plot",
            "off",
            "--no-gui",
            "--no-auto-build-rust",
            "--seed",
            "42",
            "--output-dir",
            str(tmp_path),
            *extra,
        ]
    )
    assert exit_code == 0
    manifests = sorted(tmp_path.glob("*/*/*/manifest.json"))
    assert len(manifests) == 1, manifests
    return json.loads(manifests[0].read_text(encoding="utf-8"))


def _run_dir(tmp_path: Path) -> Path:
    (manifest,) = tmp_path.glob("*/*/*/manifest.json")
    return manifest.parent


def test_snapshot_run_produces_all_artifacts(tmp_path):
    manifest = _simulate(tmp_path, "--snapshot-file", str(FIXTURE_SNAPSHOT))
    run_dir = _run_dir(tmp_path)

    assert (run_dir / "simulation.txt").is_file()
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "results.json").is_file()
    assert json.loads((run_dir / "results.json").read_text()) == manifest

    assert manifest["seed"] == 42
    assert manifest["completed_simulations"] == 2000
    assert manifest["season_id"] == 99999
    assert manifest["data_source"] == "snapshot"
    assert manifest["fetched_at"] == "2026-04-10T00:00:00Z"
    assert manifest["data_age_seconds"] > 0

    # Every team gets a full position distribution that sums to 1.
    summary = {entry["team"]: entry["positions"] for entry in manifest["position_probabilities"]}
    assert sorted(summary) == sorted(TEAMS)
    for positions in summary.values():
        assert sum(positions.values()) == pytest.approx(1.0)
        assert set(map(int, positions)) <= {1, 2, 3, 4}
    # Each finishing position is taken by exactly one team per simulated season.
    for position in ("1", "2", "3", "4"):
        assert sum(positions.get(position, 0) for positions in summary.values()) == pytest.approx(1.0)


def test_snapshot_run_text_report_lists_season_source_and_teams(tmp_path):
    _simulate(tmp_path, "--snapshot-file", str(FIXTURE_SNAPSHOT))
    text = (_run_dir(tmp_path) / "simulation.txt").read_text(encoding="utf-8")
    # The fixture carries no season name, and the report says so explicitly.
    assert "Season: unknown season (id 99999)" in text
    assert "Data source: snapshot, fetched at 2026-04-10T00:00:00Z, age " in text
    for team in TEAMS:
        assert f"{team} - " in text
    assert "(2 played)" in text  # played-matches counts come from the base table


def test_fixed_seed_is_reproducible(tmp_path):
    first = _simulate(tmp_path / "one", "--snapshot-file", str(FIXTURE_SNAPSHOT))
    second = _simulate(tmp_path / "two", "--snapshot-file", str(FIXTURE_SNAPSHOT))
    assert first["position_probabilities"] == second["position_probabilities"]
    assert first["top_tables"] == second["top_tables"]


def test_different_seed_changes_the_sampling(tmp_path):
    first = _simulate(tmp_path / "one", "--snapshot-file", str(FIXTURE_SNAPSHOT))
    exit_code = cli.main(
        [
            "simulate", "sample-league", "--snapshot-file", str(FIXTURE_SNAPSHOT),
            "--max-simulations", "2000", "--batch-size", "500", "--plot", "off",
            "--no-gui", "--no-auto-build-rust", "--seed", "43",
            "--output-dir", str(tmp_path / "two"),
        ]
    )
    assert exit_code == 0
    (manifest_path,) = (tmp_path / "two").glob("*/*/*/manifest.json")
    other = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert first["position_probabilities"] != other["position_probabilities"]


def test_leader_is_most_likely_champion(tmp_path):
    """Alpha leads on 6 pts after 2 matches; with 2 fixtures left it must be favourite."""
    manifest = _simulate(tmp_path, "--snapshot-file", str(FIXTURE_SNAPSHOT))
    champion = {
        entry["team"]: entry["positions"].get("1", 0)
        for entry in manifest["position_probabilities"]
    }
    assert max(champion, key=lambda team: champion[team]) == "Alpha FC"


# ----------------------------------------------------------------- mocked network


@pytest.fixture
def mocked_network(monkeypatch, tmp_path):
    """Route SofaScoreClient's HTTP session to a FakeSession; isolate the cache dir."""
    monkeypatch.chdir(tmp_path)  # default cache dir is relative: ./.cache/sofascore
    sessions: list[FakeSession] = []
    state: dict[str, Any] = {"routes": {}}

    def factory(*_args, **_kwargs):
        session = FakeSession(state["routes"])
        sessions.append(session)
        return session

    monkeypatch.setattr(data.requests, "Session", factory)
    state["sessions"] = sessions
    return state


def _live_routes(*, season_played=5, season_upcoming=3):
    seasons = [
        {"name": "Test League 26/27", "year": "26/27", "id": 200},
        {"name": "Test League 25/26", "year": "25/26", "id": 100},
    ]
    return make_routes(
        seasons,
        season_routes(54, 200, played=season_played, upcoming=season_upcoming),
        season_routes(54, 100, played=38, upcoming=0),
    )


def _live_args(out: Path, *extra: str) -> list[str]:
    return [
        "simulate", "la-liga-2", "--max-simulations", "1000", "--batch-size", "500",
        "--plot", "off", "--no-gui", "--no-auto-build-rust", "--seed", "7",
        "--output-dir", str(out), *extra,
    ]


def test_live_run_network_then_cache(mocked_network, tmp_path, caplog):
    mocked_network["routes"] = _live_routes()
    out = tmp_path / "out"

    with caplog.at_level(logging.INFO):
        assert cli.main(_live_args(out / "a")) == 0
    first_messages = caplog.messages
    assert any("Data from network:" in m for m in first_messages)
    assert any("Data source: network" in m for m in first_messages)
    (first_path,) = (out / "a").glob("*/*/*/manifest.json")
    first = json.loads(first_path.read_text(encoding="utf-8"))
    assert first["data_source"] == "network"
    assert first["season_id"] == 200
    assert first["season_name"] == "Test League 26/27"
    assert first["fetched_at"].endswith("Z")
    text = (first_path.parent / "simulation.txt").read_text(encoding="utf-8")
    assert "Season: Test League 26/27 (id 200)" in text
    assert "Data source: network, fetched at " in text

    caplog.clear()
    n_sessions = len(mocked_network["sessions"])
    with caplog.at_level(logging.INFO):
        assert cli.main(_live_args(out / "b")) == 0
    assert any("Data from cache:" in m and "age" in m for m in caplog.messages)
    assert any("Data source: cache" in m for m in caplog.messages)
    assert not any("Data from network:" in m for m in caplog.messages)
    assert all(not s.calls for s in mocked_network["sessions"][n_sessions:])
    (second_path,) = (out / "b").glob("*/*/*/manifest.json")
    second = json.loads(second_path.read_text(encoding="utf-8"))
    assert second["data_source"] == "cache"
    assert second["fetched_at"] == first["fetched_at"]  # original fetch time kept
    assert second["position_probabilities"] == first["position_probabilities"]


def test_live_force_refresh_ignores_cache(mocked_network, tmp_path, caplog):
    mocked_network["routes"] = _live_routes()
    assert cli.main(_live_args(tmp_path / "a")) == 0
    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert cli.main(_live_args(tmp_path / "b", "--force-refresh")) == 0
    assert any("Data source: network" in m for m in caplog.messages)
    assert not any("Data from cache:" in m for m in caplog.messages)


def test_live_no_disk_cache_does_not_write(mocked_network, tmp_path):
    mocked_network["routes"] = _live_routes()
    assert cli.main(_live_args(tmp_path / "a", "--no-disk-cache")) == 0
    assert not (tmp_path / ".cache").exists()


def test_live_run_with_only_finished_seasons_refuses(mocked_network, tmp_path):
    mocked_network["routes"] = _live_routes(season_played=38, season_upcoming=0)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(_live_args(tmp_path / "a"))
    message = str(excinfo.value)
    assert "finished" in message and "--season-year" in message
    assert not (tmp_path / "a").exists()  # no results directory for a refused run


def test_live_run_explicit_finished_season_warns_then_stops(mocked_network, tmp_path, caplog):
    mocked_network["routes"] = _live_routes()
    with caplog.at_level(logging.WARNING), pytest.raises(SystemExit) as excinfo:
        cli.main(_live_args(tmp_path / "a", "--season-year", "25/26"))
    assert any("finished" in m for m in caplog.messages)
    assert "No fixtures left" in str(excinfo.value)


def test_live_run_403_everywhere_reports_challenge(mocked_network, tmp_path, monkeypatch):
    mocked_network["routes"] = _live_routes()

    def blocked_factory(*_args, **_kwargs):
        session = FakeSession(mocked_network["routes"])
        session.block_everything = True
        return session

    monkeypatch.setattr(data.requests, "Session", blocked_factory)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(_live_args(tmp_path / "a"))
    message = str(excinfo.value)
    assert "403" in message and "challenge" in message and "--snapshot-file" in message


def test_cli_network_flags_reach_the_client(mocked_network, tmp_path):
    mocked_network["routes"] = _live_routes()
    args = _live_args(
        tmp_path / "a", "--base-url", "https://api.sofascore.com/api/v1", "--impersonate", "chrome124"
    )
    assert cli.main(args) == 0
    first_call = next(call for s in mocked_network["sessions"] for call in s.calls)
    assert first_call[0].startswith("https://api.sofascore.com/api/v1/")
    assert first_call[1] == "chrome124"
