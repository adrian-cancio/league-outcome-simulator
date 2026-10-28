"""Automatic season selection and explicit-season warnings."""

from __future__ import annotations

import pytest
from tests.support import DAY, NOW, make_routes, season_routes

from league_outcome_simulator.data import (
    STATUS_FINISHED,
    STATUS_IN_PROGRESS,
    STATUS_NOT_STARTED,
    SeasonSelectionError,
)


def _seasons(*ids_and_years):
    return [
        {"name": f"Test League {year}", "year": year, "id": season_id}
        for season_id, year in ids_and_years
    ]


def test_in_progress_season_is_selected(make_client):
    routes = make_routes(
        _seasons((200, "26/27"), (100, "25/26")),
        season_routes(54, 200, played=5, upcoming=3),
        season_routes(54, 100, played=38, upcoming=0),
    )
    selection = make_client(routes).select_season(54)
    assert selection.season_id == 200
    assert selection.status == STATUS_IN_PROGRESS
    assert selection.warnings == []


def test_newest_unstarted_season_is_skipped_for_the_one_in_progress(make_client):
    routes = make_routes(
        _seasons((300, "27/28"), (200, "26/27"), (100, "25/26")),
        season_routes(54, 300, played=0, upcoming=10),
        season_routes(54, 200, played=5, upcoming=3),
    )
    selection = make_client(routes).select_season(54)
    assert selection.season_id == 200
    assert any("27/28" in warning and "not started" in warning for warning in selection.warnings)


def test_only_unstarted_season_with_fixtures_is_used_with_a_warning(make_client):
    routes = make_routes(
        _seasons((300, "27/28"), (200, "26/27")),
        season_routes(54, 300, played=0, upcoming=10),
        season_routes(54, 200, played=38, upcoming=0),
    )
    # The finished 26/27 stops the search; the unstarted newest is the sensible pick.
    selection = make_client(routes).select_season(54)
    assert selection.season_id == 300
    assert selection.status == STATUS_NOT_STARTED
    assert any("has not started" in warning for warning in selection.warnings)


def test_finished_newest_season_is_refused_not_silently_used(make_client):
    routes = make_routes(
        _seasons((200, "26/27"), (100, "25/26")),
        season_routes(54, 200, played=38, upcoming=0),
        season_routes(54, 100, played=38, upcoming=0),
    )
    with pytest.raises(SeasonSelectionError) as excinfo:
        make_client(routes).select_season(54)
    message = str(excinfo.value)
    assert "26/27" in message and "finished" in message
    assert "--season-year" in message


def test_get_current_season_id_returns_none_instead_of_an_old_season(make_client):
    routes = make_routes(
        _seasons((200, "26/27")), season_routes(54, 200, played=38, upcoming=0)
    )
    assert make_client(routes).get_current_season_id(54) is None


def test_newest_unstarted_without_fixtures_is_refused(make_client):
    routes = make_routes(
        _seasons((300, "27/28"), (200, "26/27")),
        season_routes(54, 300, played=0, upcoming=0),
        season_routes(54, 200, played=38, upcoming=0),
    )
    with pytest.raises(SeasonSelectionError) as excinfo:
        make_client(routes).select_season(54)
    assert "27/28" in str(excinfo.value)


def test_selection_orders_by_season_year_not_list_order(make_client):
    # SofaScore normally lists newest first, but selection must not depend on it.
    routes = make_routes(
        _seasons((100, "25/26"), (200, "26/27")),
        season_routes(54, 200, played=5, upcoming=3),
        season_routes(54, 100, played=5, upcoming=3),
    )
    assert make_client(routes).select_season(54).season_id == 200


def test_explicit_finished_season_warns_but_is_honoured(make_client):
    routes = make_routes(
        _seasons((200, "26/27"), (100, "25/26")),
        season_routes(54, 200, played=5, upcoming=3),
        season_routes(54, 100, played=38, upcoming=0),
    )
    selection = make_client(routes).select_season(54, season_year="25/26")
    assert selection.season_id == 100
    assert selection.status == STATUS_FINISHED
    assert any("finished" in warning for warning in selection.warnings)


def test_explicit_unknown_year_raises(make_client):
    routes = make_routes(_seasons((200, "26/27")), season_routes(54, 200, played=5, upcoming=3))
    with pytest.raises(ValueError, match="not found"):
        make_client(routes).select_season(54, season_year="01/02")


def test_explicit_season_id_wins(make_client):
    routes = make_routes(
        _seasons((200, "26/27"), (100, "25/26")),
        season_routes(54, 200, played=5, upcoming=3),
        season_routes(54, 100, played=38, upcoming=0),
    )
    assert make_client(routes).resolve_season_id(54, season_id=100) == 100


def test_season_state_counts_matches_played(make_client):
    routes = make_routes(
        _seasons((200, "26/27")), season_routes(54, 200, played=5, upcoming=3)
    )
    state = make_client(routes).season_state(54, 200)
    assert state.matches_played == 10  # 4 teams x 5 played / 2
    assert state.has_fixtures and not state.settled


def test_finished_season_state_settles_only_after_a_delay(make_client, tmp_path):
    recent = make_routes(
        _seasons((200, "26/27")),
        season_routes(54, 200, played=38, upcoming=0, last_start=NOW - DAY),
    )
    old = make_routes(
        _seasons((200, "26/27")),
        season_routes(54, 200, played=38, upcoming=0, last_start=NOW - 30 * DAY),
    )
    # Separate cache dirs: the two scenarios must not see each other's cached probes.
    assert not make_client(recent, cache_dir=tmp_path / "a").season_state(54, 200).settled
    assert make_client(old, cache_dir=tmp_path / "b").season_state(54, 200).settled
