"""Opt-in smoke test against the real SofaScore API.

Skipped unless ``LOS_LIVE_TESTS=1``. Run it with::

    LOS_LIVE_TESTS=1 ./venv/bin/python -m pytest -m live -q
"""

from __future__ import annotations

import json
import os

import pytest

from league_outcome_simulator import cli


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("LOS_LIVE_TESTS") != "1",
        reason="live SofaScore test; set LOS_LIVE_TESTS=1 to run it",
    ),
]


def test_la_liga_2_live_simulation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # fresh cache dir: guarantees a real network fetch
    out = tmp_path / "out"
    exit_code = cli.main(
        [
            "simulate", "la-liga-2", "--plot", "off", "--no-gui",
            "--max-simulations", "2000", "--batch-size", "1000", "--seed", "1",
            "--output-dir", str(out),
        ]
    )
    assert exit_code == 0

    (manifest_path,) = out.glob("*/*/*/manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["data_source"] == "network"
    assert manifest["season_id"]
    assert manifest["completed_simulations"] == 2000
    assert len(manifest["position_probabilities"]) >= 18
    assert (manifest_path.parent / "simulation.txt").is_file()
