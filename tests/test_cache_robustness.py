"""Unique temporary names for cache writes and the ``cache clean`` subcommand."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from tests.support import make_routes, season_routes

from league_outcome_simulator import cli
from league_outcome_simulator.data import (
    FetchRecord,
    SOURCE_NETWORK,
    _MemoryEntry,
    legacy_cache_files,
)


SEASONS_PATH = "/unique-tournament/54/seasons"
SEASONS = [{"name": "Test League 26/27", "year": "26/27", "id": 200}]


def _routes():
    return make_routes(SEASONS, season_routes(54, 200, played=5, upcoming=3))


def _entry(payload, fetched_at: float = 1.0) -> _MemoryEntry:
    return _MemoryEntry(
        payload,
        FetchRecord(SEASONS_PATH, "derived", "seasons", SOURCE_NETWORK, fetched_at),
    )


# ------------------------------------------------------------ unique tmp names


def test_each_write_uses_its_own_tmp_name(make_client, cache_dir, monkeypatch):
    client = make_client(_routes())
    seen: list[str] = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(Path(src).name)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    client._write_disk(_entry({"seasons": []}), finished=False)
    client._write_disk(_entry({"seasons": []}), finished=False)

    assert len(seen) == 2 and seen[0] != seen[1]
    final = client._cache_path(SEASONS_PATH).name
    for name in seen:
        assert name.startswith(f"{final}.{os.getpid()}.") and name.endswith(".tmp")
    assert list(cache_dir.glob("*.tmp")) == []


def test_two_writers_to_one_entry_do_not_collide(make_client, cache_dir, monkeypatch):
    """Writer B starts and finishes while writer A is between write and replace."""
    client = make_client(_routes())
    target = client._cache_path(SEASONS_PATH)
    real_replace = os.replace
    state = {"nested": False}

    def interleaving_replace(src, dst):
        if not state["nested"]:
            state["nested"] = True
            # A's tmp file is on disk right now; B writes and publishes its own.
            assert Path(src).exists()
            client._write_disk(_entry({"writer": "B"}, 2.0), finished=False)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", interleaving_replace)
    client._write_disk(_entry({"writer": "A"}, 1.0), finished=False)

    # Both writes completed intact: with a shared tmp name, A's replace would have
    # failed (file already moved by B) and nothing of A would have been published.
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["payload"] == {"writer": "A"}  # A replaced last
    assert list(cache_dir.glob("*.tmp")) == []
    assert [p.name for p in cache_dir.iterdir()] == [target.name]


def test_forced_name_collision_preserves_both_writes(make_client, cache_dir, monkeypatch, caplog):
    from league_outcome_simulator import data

    client = make_client(_routes())
    suffixes = iter(["collision", "collision", "retry"])
    monkeypatch.setattr(data.secrets, "token_hex", lambda _: next(suffixes))
    real_replace = os.replace
    published = []

    def interleave(src, dst):
        if not published:
            published.append("started")
            client._write_disk(_entry({"writer": "B"}), finished=False)
        published.append(json.loads(Path(src).read_text())["payload"])
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", interleave)
    with caplog.at_level("WARNING", logger="league_outcome_simulator.data"):
        client._write_disk(_entry({"writer": "A"}), finished=False)
    assert published == ["started", {"writer": "B"}, {"writer": "A"}]
    assert not caplog.records
    assert not list(cache_dir.glob("*.tmp"))


def test_concurrent_threads_leave_a_valid_entry_and_no_tmp_files(make_client, cache_dir, caplog):
    client = make_client(_routes())
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def writer(index: int) -> None:
        try:
            barrier.wait()
            for round_ in range(20):
                client._write_disk(_entry({"writer": index, "round": round_}), finished=False)
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert not [record for record in caplog.records if record.levelno >= 30]
    document = json.loads(client._cache_path(SEASONS_PATH).read_text(encoding="utf-8"))
    assert document["payload"]["writer"] in range(8)
    assert list(cache_dir.glob("*.tmp")) == []


def test_collision_exhaustion_warns_without_touching_reserved_file(make_client, monkeypatch, caplog):
    from league_outcome_simulator import data

    client = make_client(_routes())
    monkeypatch.setattr(data.secrets, "token_hex", lambda _: "reserved")
    target = client._cache_path(SEASONS_PATH)
    target.parent.mkdir(parents=True)
    reserved = target.with_name(f"{target.name}.{os.getpid()}.reserved.tmp")
    reserved.write_text("other writer", encoding="utf-8")
    client._write_disk(_entry({"writer": "A"}), finished=False)
    assert reserved.read_text(encoding="utf-8") == "other writer"
    assert not target.exists()
    assert any("after 10 attempts" in record.message for record in caplog.records)


def test_failed_write_cleans_up_its_tmp_file(make_client, cache_dir, monkeypatch, caplog):
    client = make_client(_routes())

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with caplog.at_level("WARNING", logger="league_outcome_simulator.data"):
        client._write_disk(_entry({"seasons": []}), finished=False)  # must not raise

    assert list(cache_dir.glob("*.tmp")) == []
    assert any("Could not write cache file" in m for m in caplog.messages)


# ------------------------------------------------------------ cache clean


def _populate(cache_dir: Path) -> dict[str, str]:
    cache_dir.mkdir(parents=True)
    files = {
        "0000177117.json": "{}",
        "1234567890.json": '{"old": true}',
        "notes.json": "[]",
        "v2-" + "a" * 32 + ".json": '{"schema": 2}',
        "v2-" + "b" * 32 + ".json.99.deadbeef.tmp": "partial",
        "README.txt": "keep me",
    }
    for name, body in files.items():
        (cache_dir / name).write_text(body, encoding="utf-8")
    (cache_dir / "subdir.json").mkdir()  # a directory named like a cache file
    return files


def _snapshot(cache_dir: Path) -> dict[str, str | None]:
    return {
        p.name: (p.read_text(encoding="utf-8") if p.is_file() else None)
        for p in sorted(cache_dir.iterdir())
    }


LEGACY = ["0000177117.json", "1234567890.json", "notes.json"]


def test_legacy_cache_files_selects_only_plain_non_v2_json(tmp_path):
    cache_dir = tmp_path / "c"
    _populate(cache_dir)
    assert [p.name for p in legacy_cache_files(cache_dir)] == sorted(LEGACY)


def test_legacy_cache_files_on_missing_dir_is_empty(tmp_path):
    assert legacy_cache_files(tmp_path / "nope") == []


def test_cache_clean_dry_run_lists_and_keeps_everything(tmp_path, capsys):
    cache_dir = tmp_path / "c"
    _populate(cache_dir)
    before = _snapshot(cache_dir)

    assert cli.main(["cache", "clean", "--cache-dir", str(cache_dir), "--dry-run"]) == 0

    out = capsys.readouterr().out
    for name in LEGACY:
        assert name in out
    assert "v2-" not in out and "README.txt" not in out
    assert "would be removed" in out
    assert _snapshot(cache_dir) == before


def test_cache_clean_removes_only_legacy_files(tmp_path, capsys):
    cache_dir = tmp_path / "c"
    files = _populate(cache_dir)

    assert cli.main(["cache", "clean", "--cache-dir", str(cache_dir)]) == 0

    out = capsys.readouterr().out
    assert f"Removed {len(LEGACY)} of {len(LEGACY)}" in out
    remaining = _snapshot(cache_dir)
    assert set(remaining) == (set(files) - set(LEGACY)) | {"subdir.json"}
    for name in remaining.keys() - {"subdir.json"}:
        assert remaining[name] == files[name]  # untouched content


def test_cache_clean_lists_before_deleting(tmp_path, capsys):
    cache_dir = tmp_path / "c"
    _populate(cache_dir)
    cli.main(["cache", "clean", "--cache-dir", str(cache_dir)])
    out = capsys.readouterr().out
    assert out.index("0000177117.json") < out.index("Removed")


def test_cache_clean_with_nothing_to_do(tmp_path, capsys):
    cache_dir = tmp_path / "c"
    cache_dir.mkdir()
    (cache_dir / ("v2-" + "a" * 32 + ".json")).write_text("{}", encoding="utf-8")

    assert cli.main(["cache", "clean", "--cache-dir", str(cache_dir)]) == 0
    assert "No legacy cache files" in capsys.readouterr().out
    assert len(list(cache_dir.iterdir())) == 1


def test_cache_clean_on_missing_dir_succeeds(tmp_path, capsys):
    assert cli.main(["cache", "clean", "--cache-dir", str(tmp_path / "missing")]) == 0
    assert "No legacy cache files" in capsys.readouterr().out


def test_cache_clean_does_not_follow_symlinks(tmp_path):
    cache_dir = tmp_path / "c"
    cache_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("precious", encoding="utf-8")
    try:
        (cache_dir / "0000000001.json").symlink_to(outside)
    except OSError:  # pragma: no cover - platforms without symlink rights
        pytest.skip("symlinks not available")

    assert cli.main(["cache", "clean", "--cache-dir", str(cache_dir)]) == 0
    assert outside.read_text(encoding="utf-8") == "precious"
    assert (cache_dir / "0000000001.json").is_symlink()


def test_cache_clean_defaults_to_the_project_cache_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # the relative default resolves inside tmp_path
    default = tmp_path / ".cache" / "sofascore"
    default.mkdir(parents=True)
    (default / "0000000001.json").write_text("{}", encoding="utf-8")

    assert cli.main(["cache", "clean", "--dry-run"]) == 0
    assert "0000000001.json" in capsys.readouterr().out
    assert (default / "0000000001.json").exists()


def test_cache_requires_a_subcommand():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["cache"])
    assert excinfo.value.code == 2
