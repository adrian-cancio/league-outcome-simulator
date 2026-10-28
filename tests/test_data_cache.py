"""Disk cache metadata, TTL expiry, legacy-entry handling and cache keys."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support import DAY, NOW, make_routes, season_routes

from league_outcome_simulator.data import (
    CACHE_SCHEMA,
    LEGACY_BASE_URL,
    SOURCE_CACHE,
    SOURCE_NETWORK,
    iso_utc,
)
from league_outcome_simulator.utils import stable_seed_from_text


SEASONS_PATH = "/unique-tournament/54/seasons"
SEASONS = [{"name": "Test League 26/27", "year": "26/27", "id": 200}]


def _routes(**kwargs):
    return make_routes(SEASONS, season_routes(54, 200, played=5, upcoming=3, **kwargs))


def _v2_files(cache_dir: Path) -> list[Path]:
    return sorted(cache_dir.glob("v2-*.json"))


def test_entry_carries_metadata(make_client, cache_dir):
    client = make_client(_routes())
    client.get_seasons(54)

    (entry_file,) = _v2_files(cache_dir)
    document = json.loads(entry_file.read_text(encoding="utf-8"))
    assert document["schema"] == CACHE_SCHEMA
    assert document["url"] == f"https://www.sofascore.com/api/v1{SEASONS_PATH}"
    assert document["path"] == SEASONS_PATH
    assert document["fetched_at"] == iso_utc(NOW)
    assert document["fetched_at_ts"] == NOW
    assert document["payload"] == {"seasons": SEASONS}


def test_fresh_entry_is_served_from_cache_with_its_age(make_client, clock):
    first = make_client(_routes())
    first.get_seasons(54)
    assert first.records[SEASONS_PATH].source == SOURCE_NETWORK

    clock.advance(600)
    second = make_client(_routes())
    assert second.get_seasons(54) == SEASONS
    assert second.session.calls == []  # nothing hit the network
    record = second.records[SEASONS_PATH]
    assert record.source == SOURCE_CACHE
    assert record.fetched_at == NOW  # the original fetch time, not "now"
    assert second.data_summary()["data_age_seconds"] == 600


def test_cache_hit_is_logged_with_age(make_client, clock, caplog):
    make_client(_routes()).get_seasons(54)
    clock.advance(125)
    with caplog.at_level("INFO", logger="league_outcome_simulator.data"):
        make_client(_routes()).get_seasons(54)
    assert any(
        "Data from cache" in message and "age 2m 05s" in message
        for message in caplog.messages
    )


def test_seasons_list_expires_after_one_day(make_client, clock):
    make_client(_routes()).get_seasons(54)

    clock.advance(DAY - 60)
    still_fresh = make_client(_routes())
    still_fresh.get_seasons(54)
    assert still_fresh.session.calls == []

    clock.advance(120)
    expired = make_client(_routes())
    expired.get_seasons(54)
    assert len(expired.session.calls) == 1
    assert expired.records[SEASONS_PATH].source == SOURCE_NETWORK


def test_current_season_data_expires_quickly(make_client, clock):
    # 3 fixtures left -> in progress -> short TTL (30 minutes)
    make_client(_routes()).get_league_table(54, 200)

    clock.advance(29 * 60)
    fresh = make_client(_routes())
    fresh.get_league_table(54, 200)
    assert fresh.session.calls == []

    clock.advance(2 * 60)
    stale = make_client(_routes())
    stale.get_league_table(54, 200)
    assert any("standings/total" in call[0] for call in stale.session.calls)


def test_finished_season_data_is_kept_long(make_client, clock):
    # no fixtures left, last match long ago -> settled -> long TTL
    routes = make_routes(SEASONS, season_routes(54, 200, played=38, upcoming=0))

    first = make_client(routes)
    assert first.season_state(54, 200).settled
    first.get_league_table(54, 200)

    clock.advance(10 * DAY)
    later = make_client(routes)
    later.get_league_table(54, 200)
    later.get_league_table(54, 200)
    assert [c for c in later.session.calls if "standings/total" in c[0]] == []
    assert later.records["/unique-tournament/54/season/200/standings/total"].source == SOURCE_CACHE


def test_recently_finished_season_is_not_trusted_for_long(make_client, clock):
    # Last match yesterday: the final table may still be corrected, so no long TTL.
    routes = make_routes(
        SEASONS, season_routes(54, 200, played=38, upcoming=0, last_start=NOW - DAY)
    )
    first = make_client(routes)
    assert not first.season_state(54, 200).settled
    first.get_league_table(54, 200)

    clock.advance(3 * 3600)
    later = make_client(routes)
    later.get_league_table(54, 200)
    assert any("standings/total" in call[0] for call in later.session.calls)


def test_legacy_cache_file_is_ignored_but_not_deleted(make_client, cache_dir):
    legacy_url = f"{LEGACY_BASE_URL}{SEASONS_PATH}"
    legacy_file = cache_dir / f"{stable_seed_from_text(legacy_url):010d}.json"
    cache_dir.mkdir(parents=True)
    stale_body = json.dumps({"seasons": [{"name": "OLD 25/26", "year": "25/26", "id": 1}]})
    legacy_file.write_text(stale_body, encoding="utf-8")

    client = make_client(_routes())
    assert client.get_seasons(54) == SEASONS  # fresh network data, not the legacy one
    assert client.records[SEASONS_PATH].source == SOURCE_NETWORK
    assert len(client.session.calls) == 1
    assert legacy_file.read_text(encoding="utf-8") == stale_body  # untouched


def test_legacy_file_logged_as_ignored(make_client, cache_dir, caplog):
    legacy_url = f"{LEGACY_BASE_URL}{SEASONS_PATH}"
    cache_dir.mkdir(parents=True)
    (cache_dir / f"{stable_seed_from_text(legacy_url):010d}.json").write_text(
        json.dumps({"seasons": []}), encoding="utf-8"
    )
    with caplog.at_level("INFO", logger="league_outcome_simulator.data"):
        make_client(_routes()).get_seasons(54)
    assert any("legacy cache file" in message for message in caplog.messages)


def test_metadata_less_file_at_current_path_is_stale(make_client, cache_dir):
    client = make_client(_routes())
    path = client._cache_path(SEASONS_PATH)
    cache_dir.mkdir(parents=True)
    path.write_text(json.dumps({"seasons": [{"name": "OLD", "id": 1}]}), encoding="utf-8")

    assert client.get_seasons(54) == SEASONS
    assert client.records[SEASONS_PATH].source == SOURCE_NETWORK


def test_corrupt_cache_file_is_refetched(make_client, cache_dir):
    client = make_client(_routes())
    cache_dir.mkdir(parents=True)
    client._cache_path(SEASONS_PATH).write_text("{not json", encoding="utf-8")
    assert client.get_seasons(54) == SEASONS


def test_future_dated_entry_is_distrusted(make_client, clock):
    make_client(_routes()).get_seasons(54)
    clock.advance(-3600)  # clock now an hour *before* the entry was written
    client = make_client(_routes())
    client.get_seasons(54)
    assert len(client.session.calls) == 1


def test_new_season_never_reuses_old_season_entries(make_client, cache_dir):
    seasons = [
        {"name": "Test League 26/27", "year": "26/27", "id": 200},
        {"name": "Test League 25/26", "year": "25/26", "id": 100},
    ]
    # Same URL shape, different season ids -> different files, different payloads.
    old = season_routes(54, 100, played=38, upcoming=0)
    new = season_routes(54, 200, played=5, upcoming=3)
    routes = make_routes(seasons, old, new)
    client = make_client(routes)
    table_old = client.get_league_table(54, 100)
    table_new = client.get_league_table(54, 200)
    assert table_old != table_new
    assert len({p.name for p in _v2_files(cache_dir)}) >= 2

    reread = make_client(routes)
    assert reread.get_league_table(54, 200) == table_new
    assert reread.get_league_table(54, 100) == table_old


def test_cache_key_ignores_host_but_not_season(make_client):
    client = make_client({})
    a = client._cache_path("https://api.sofascore.com/api/v1/unique-tournament/54/season/1/standings/total")
    b = client._cache_path("https://www.sofascore.com/api/v1/unique-tournament/54/season/1/standings/total")
    c = client._cache_path("https://www.sofascore.com/api/v1/unique-tournament/54/season/2/standings/total")
    assert a == b
    assert a != c


def test_force_refresh_bypasses_fresh_cache(make_client):
    make_client(_routes()).get_seasons(54)
    forced = make_client(_routes(), force_refresh=True)
    forced.get_seasons(54)
    forced.get_seasons(54)  # second read in the same run must not refetch again
    assert len(forced.session.calls) == 1
    assert forced.records[SEASONS_PATH].source == SOURCE_NETWORK


def test_no_disk_cache_neither_reads_nor_writes(make_client, cache_dir):
    make_client(_routes()).get_seasons(54)  # populates the cache
    before = {p.name: p.read_bytes() for p in cache_dir.glob("*.json")}

    client = make_client(_routes(), use_disk_cache=False)
    client.get_seasons(54)
    assert len(client.session.calls) == 1  # did not read the populated cache
    assert {p.name: p.read_bytes() for p in cache_dir.glob("*.json")} == before


def test_data_summary_reports_mixed_sources(make_client, clock):
    first = make_client(_routes())
    first.get_league_table(54, 200)
    clock.advance(60)
    second = make_client(_routes())
    second.get_league_table(54, 200)  # cache
    second.get_home_league_table(54, 200)  # network (never fetched before)
    summary = second.data_summary()
    assert summary["data_source"] == "mixed"
    assert summary["fetched_at"] == iso_utc(NOW)  # oldest component
    assert summary["cache_hits"] >= 1 and summary["network_requests"] >= 1
