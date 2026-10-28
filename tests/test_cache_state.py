"""Season ``_state`` cache entries: disk hits, malformed data, TTL, --force-refresh."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.support import DAY, NOW, make_routes, season_routes

from league_outcome_simulator.data import (
    CACHE_SCHEMA,
    STATUS_FINISHED,
    STATUS_IN_PROGRESS,
    iso_utc,
)


STATE_PATH = "/unique-tournament/54/season/200/_state"
STANDINGS_PATH = "/unique-tournament/54/season/200/standings/total"
SEASONS = [{"name": "Test League 26/27", "year": "26/27", "id": 200}]

# 4 teams x 5 played each, every match counted twice -> 10 (see support.standings_payload)
NETWORK_MATCHES_PLAYED = 10

# Deliberately unlike anything the fake network produces, so a disk hit and a
# recomputation can never be confused.
DISK_STATE = {
    "status": STATUS_FINISHED,
    "matches_played": 99,
    "has_fixtures": False,
    "settled": True,
    "pending": 0,
}


def _in_progress():
    return make_routes(SEASONS, season_routes(54, 200, played=5, upcoming=3))


def _settled():
    return make_routes(SEASONS, season_routes(54, 200, played=38, upcoming=0))


def _recently_finished():
    return make_routes(
        SEASONS, season_routes(54, 200, played=38, upcoming=0, last_start=NOW - DAY)
    )


def _state_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": CACHE_SCHEMA,
        "url": "derived",
        "path": STATE_PATH,
        "kind": "season-data",
        "fetched_at": iso_utc(NOW),
        "fetched_at_ts": NOW,
        "finished": False,
        "payload": dict(DISK_STATE),
    }
    document.update(overrides)
    return document


def _seed_state(client, **overrides: Any) -> None:
    path = client._cache_path(STATE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_state_document(**overrides)), encoding="utf-8")


def _urls(client) -> list[str]:
    return [url for url, _ in client.session.calls]


# ------------------------------------------------------------------ disk hit


def test_state_entry_on_disk_is_used_without_network(make_client):
    client = make_client(_in_progress())
    _seed_state(client)

    state = client.season_state(54, 200)

    assert state.status == STATUS_FINISHED
    assert state.matches_played == 99
    assert state.settled
    assert client.session.calls == []


def test_state_round_trips_through_disk(make_client, clock):
    first = make_client(_in_progress())
    original = first.season_state(54, 200)
    assert original.status == STATUS_IN_PROGRESS

    clock.advance(60)
    second = make_client(_in_progress())
    assert second.season_state(54, 200) == original
    assert second.session.calls == []


# ------------------------------------------------------------------ malformed


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "finished",
        None,
        {},
        {"status": STATUS_FINISHED},  # required fields missing
        {**DISK_STATE, "unexpected": 1},  # unknown field
    ],
    ids=["list", "string", "null", "empty", "partial", "extra-key"],
)
def test_malformed_state_payload_is_recomputed(make_client, payload):
    client = make_client(_in_progress())
    _seed_state(client, payload=payload)

    state = client.season_state(54, 200)

    assert state.status == STATUS_IN_PROGRESS  # what the network says, not the disk
    assert state.matches_played == NETWORK_MATCHES_PLAYED
    assert any("standings/total" in url for url in _urls(client))


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema": 1},
        {"path": "/unique-tournament/54/season/999/_state"},
        {"fetched_at_ts": "yesterday"},
        {"fetched_at_ts": True},
        {"fetched_at_ts": None},
    ],
    ids=["old-schema", "wrong-path", "string-ts", "bool-ts", "null-ts"],
)
def test_malformed_state_envelope_is_ignored(make_client, overrides):
    client = make_client(_in_progress())
    _seed_state(client, **overrides)

    assert client.season_state(54, 200).status == STATUS_IN_PROGRESS
    assert client.session.calls  # recomputed from the network


def test_state_entry_without_payload_is_ignored(make_client):
    client = make_client(_in_progress())
    document = _state_document()
    del document["payload"]
    path = client._cache_path(STATE_PATH)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document), encoding="utf-8")

    assert client.season_state(54, 200).matches_played == NETWORK_MATCHES_PLAYED


def test_recomputed_state_overwrites_malformed_entry(make_client):
    client = make_client(_in_progress())
    _seed_state(client, payload=[])
    client.season_state(54, 200)

    document = json.loads(client._cache_path(STATE_PATH).read_text(encoding="utf-8"))
    assert document["payload"]["status"] == STATUS_IN_PROGRESS
    assert document["payload"]["matches_played"] == NETWORK_MATCHES_PLAYED


@pytest.mark.parametrize("field,value", [
    ("status", "bogus"), ("status", []),
    ("matches_played", -1), ("matches_played", True),
    ("matches_played", 1.5), ("matches_played", "10"),
    ("pending", -1), ("pending", False), ("pending", "0"),
    ("has_fixtures", 1), ("has_fixtures", "false"),
    ("settled", "false"), ("settled", 0),
])
def test_invalid_typed_state_fields_are_recomputed(make_client, field, value):
    client = make_client(_in_progress())
    _seed_state(client, payload={**DISK_STATE, field: value})
    state = client.season_state(54, 200)
    assert state.status == STATUS_IN_PROGRESS
    assert state.matches_played == NETWORK_MATCHES_PLAYED
    assert client.session.calls


@pytest.mark.parametrize(
    "timestamp", [float("nan"), float("inf"), -float("inf"), 10**400]
)
def test_nonfinite_cache_timestamps_are_recomputed(make_client, timestamp):
    client = make_client(_in_progress())
    _seed_state(client, fetched_at_ts=timestamp)
    assert client.season_state(54, 200).status == STATUS_IN_PROGRESS
    assert client.session.calls


# ------------------------------------------------------------------ TTL


def test_unsettled_state_expires_at_exactly_the_current_season_ttl(make_client, clock):
    ttl = 30 * 60.0
    make_client(_in_progress()).season_state(54, 200)

    clock.advance(ttl - 1)
    fresh = make_client(_in_progress())
    fresh.season_state(54, 200)
    assert fresh.session.calls == []

    clock.advance(1)  # age == ttl: the boundary counts as expired
    expired = make_client(_in_progress())
    expired.season_state(54, 200)
    assert expired.session.calls


def test_settled_state_is_kept_for_the_long_ttl(make_client, clock):
    long_ttl = 30 * DAY
    assert make_client(_settled()).season_state(54, 200).settled

    clock.advance(long_ttl - 60)
    kept = make_client(_settled())
    assert kept.season_state(54, 200).settled
    assert kept.session.calls == []

    clock.advance(120)
    expired = make_client(_settled())
    assert expired.season_state(54, 200).settled
    assert expired.session.calls  # long TTL elapsed -> recomputed


def test_state_turns_settled_once_the_settle_delay_has_passed(make_client, clock):
    # Last match was yesterday: finished but not settled -> short TTL.
    first = make_client(_recently_finished())
    state = first.season_state(54, 200)
    assert state.status == STATUS_FINISHED and not state.settled

    # 36 hours later the entry has expired and the same season now counts as settled.
    clock.advance(36 * 3600)
    later = make_client(_recently_finished())
    assert later.season_state(54, 200).settled
    assert later.session.calls

    # The new settled entry is then trusted for the long TTL.
    clock.advance(7 * DAY)
    trusted = make_client(_recently_finished())
    assert trusted.season_state(54, 200).settled
    assert trusted.session.calls == []


def test_future_dated_state_entry_is_distrusted(make_client):
    client = make_client(_in_progress())
    _seed_state(client, fetched_at_ts=NOW + 3600)

    assert client.season_state(54, 200).status == STATUS_IN_PROGRESS
    assert client.session.calls


def test_finished_flag_of_the_entry_selects_the_ttl(make_client, clock):
    # A settled-flagged entry survives a time that would expire an unsettled one.
    clock.advance(0)
    settled_entry = make_client(_in_progress())
    _seed_state(settled_entry, finished=True)
    clock.advance(2 * DAY)
    assert settled_entry.season_state(54, 200).status == STATUS_FINISHED
    assert settled_entry.session.calls == []

    unsettled_entry = make_client(_in_progress())
    _seed_state(unsettled_entry, finished=False)
    assert unsettled_entry.season_state(54, 200).status == STATUS_IN_PROGRESS
    assert unsettled_entry.session.calls


# ------------------------------------------------------------------ force refresh


def test_force_refresh_ignores_a_fresh_state_entry(make_client):
    client = make_client(_in_progress(), force_refresh=True)
    _seed_state(client)

    state = client.season_state(54, 200)

    assert state.status == STATUS_IN_PROGRESS  # not the seeded "finished"
    assert any("standings/total" in url for url in _urls(client))


def test_force_refresh_refetches_each_probe_once_and_rewrites_entries(make_client, clock):
    routes = _in_progress()
    make_client(routes).season_state(54, 200)

    clock.advance(600)
    forced = make_client(routes, force_refresh=True)
    forced.season_state(54, 200)
    forced.get_league_table(54, 200)  # a probe already refreshed in this run

    urls = _urls(forced)
    assert any("events/next/0" in url for url in urls)
    assert any("standings/total" in url for url in urls)
    assert len(urls) == len(set(urls))  # nothing fetched twice in one run

    # Both the state and the probe entries were rewritten with the new fetch time.
    for path in (STATE_PATH, STANDINGS_PATH):
        document = json.loads(forced._cache_path(path).read_text(encoding="utf-8"))
        assert document["fetched_at_ts"] == NOW + 600

    # ... so a later normal run is served entirely from disk.
    clock.advance(60)
    normal = make_client(routes)
    normal.season_state(54, 200)
    normal.get_league_table(54, 200)
    assert normal.session.calls == []


def test_force_refresh_on_a_settled_season_keeps_the_long_ttl_flag(make_client, clock):
    routes = _settled()
    make_client(routes).season_state(54, 200)

    clock.advance(600)
    forced = make_client(routes, force_refresh=True)
    assert forced.season_state(54, 200).settled

    for path in (STATE_PATH, STANDINGS_PATH):
        document = json.loads(forced._cache_path(path).read_text(encoding="utf-8"))
        assert document["finished"] is True, path

    clock.advance(10 * DAY)
    later = make_client(routes)
    assert later.season_state(54, 200).settled
    later.get_league_table(54, 200)
    assert later.session.calls == []


def test_force_refresh_without_disk_cache_writes_nothing(make_client, cache_dir):
    client = make_client(_in_progress(), force_refresh=True, use_disk_cache=False)
    assert client.season_state(54, 200).status == STATUS_IN_PROGRESS
    assert not cache_dir.exists()
