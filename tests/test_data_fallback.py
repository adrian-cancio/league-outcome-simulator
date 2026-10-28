"""HTTP 403 handling: host/impersonation fallback and the final error."""

from __future__ import annotations

import pytest
from tests.support import make_routes, season_routes

from league_outcome_simulator.data import (
    DEFAULT_BASE_URL,
    DEFAULT_IMPERSONATE,
    FALLBACK_BASE_URLS,
    SofaScoreBlockedError,
    SofaScoreError,
)


SEASONS = [{"name": "Test League 26/27", "year": "26/27", "id": 200}]
ROUTES = make_routes(SEASONS, season_routes(54, 200, played=5, upcoming=3))
SEASONS_PATH = "/unique-tournament/54/seasons"


def test_default_is_the_working_host_and_profile(make_client):
    client = make_client(ROUTES)
    client.get_seasons(54)
    assert client.session.calls == [(f"{DEFAULT_BASE_URL}{SEASONS_PATH}", DEFAULT_IMPERSONATE)]
    assert DEFAULT_BASE_URL.startswith("https://www.sofascore.com/")
    assert DEFAULT_IMPERSONATE.startswith("safari")


def test_403_on_primary_profile_falls_back_to_another_profile(make_client, caplog):
    client = make_client(ROUTES)
    client.session.blocked.add((DEFAULT_BASE_URL, DEFAULT_IMPERSONATE))
    with caplog.at_level("WARNING", logger="league_outcome_simulator.data"):
        assert client.get_seasons(54) == SEASONS

    profiles = [profile for _, profile in client.session.calls]
    assert profiles[0] == DEFAULT_IMPERSONATE
    assert profiles[-1] != DEFAULT_IMPERSONATE
    assert any("HTTP 403" in m and "challenge" in m for m in caplog.messages)
    assert any("Fallback succeeded" in m for m in caplog.messages)


def test_403_on_primary_host_falls_back_to_other_host(make_client):
    client = make_client(ROUTES)
    # Block the primary host for every profile; only the alternative host works.
    for profile in ("safari17_0", "safari18_0", "safari15_5", "chrome124"):
        client.session.blocked.add((DEFAULT_BASE_URL, profile))
    assert client.get_seasons(54) == SEASONS
    assert client.session.calls[-1][0].startswith(FALLBACK_BASE_URLS[0])


def test_working_combination_is_remembered(make_client):
    client = make_client(ROUTES)
    client.session.blocked.add((DEFAULT_BASE_URL, DEFAULT_IMPERSONATE))
    client.get_seasons(54)
    calls_after_first = len(client.session.calls)
    client.get_league_table(54, 200)
    new_calls = client.session.calls[calls_after_first:]
    # Straight to the combination that worked: no further blocked attempts.
    assert new_calls
    assert {profile for _, profile in new_calls} == {client._active[1]}
    assert DEFAULT_IMPERSONATE not in {profile for _, profile in new_calls}


def test_everything_blocked_fails_clearly(make_client):
    client = make_client(ROUTES)
    client.session.block_everything = True
    with pytest.raises(SofaScoreBlockedError) as excinfo:
        client.get_seasons(54)
    message = str(excinfo.value)
    assert "403" in message
    assert "challenge" in message
    assert "--snapshot-file" in message
    assert isinstance(excinfo.value, SofaScoreError)
    # Every alternative was really tried (2 hosts x 4 profiles), once each: a 403
    # is a block, not a transient error, so it is not retried.
    assert len(client.session.calls) == 8
    assert len(set(client.session.calls)) == 8


def test_fallback_can_be_disabled(make_client):
    client = make_client(ROUTES, fallback=False)
    client.session.blocked.add((DEFAULT_BASE_URL, DEFAULT_IMPERSONATE))
    with pytest.raises(SofaScoreBlockedError):
        client.get_seasons(54)
    assert len(client.session.calls) == 1


def test_constructor_overrides_are_tried_first(make_client):
    client = make_client(
        ROUTES, base_url="https://api.sofascore.com/api/v1/", impersonate="chrome124"
    )
    client.get_seasons(54)
    assert client.session.calls[0] == (
        f"https://api.sofascore.com/api/v1{SEASONS_PATH}",
        "chrome124",
    )


def test_environment_variables_configure_the_client(make_client, monkeypatch):
    monkeypatch.setenv("SOFASCORE_BASE_URL", "https://api.sofascore.com/api/v1")
    monkeypatch.setenv("SOFASCORE_IMPERSONATE", "safari18_0")
    client = make_client(ROUTES)
    client.get_seasons(54)
    assert client.session.calls[0] == (
        f"https://api.sofascore.com/api/v1{SEASONS_PATH}",
        "safari18_0",
    )


def test_explicit_argument_beats_environment(make_client, monkeypatch):
    monkeypatch.setenv("SOFASCORE_IMPERSONATE", "safari18_0")
    client = make_client(ROUTES, impersonate="safari15_5")
    client.get_seasons(54)
    assert client.session.calls[0][1] == "safari15_5"


def test_blocked_fetch_is_not_cached(make_client, cache_dir):
    client = make_client(ROUTES)
    client.session.block_everything = True
    with pytest.raises(SofaScoreBlockedError):
        client.get_seasons(54)
    assert not list(cache_dir.glob("*.json"))


def test_missing_page_is_404_not_a_block(make_client):
    client = make_client(ROUTES)
    # events/last/0 does not exist for this route table's other season id
    assert client.get_remaining_fixtures(54, 999) == []
