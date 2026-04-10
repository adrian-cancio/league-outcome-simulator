from __future__ import annotations

import json
from pathlib import Path

from league_outcome_simulator import cli


FIXTURE_SNAPSHOT = Path(__file__).parent / "fixtures" / "sample_snapshot.json"


def test_leagues_command(capsys):
    exit_code = cli.main(["leagues"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "premier-league" in captured.out


def test_simulate_snapshot_no_gui(tmp_path):
    exit_code = cli.main(
        [
            "simulate",
            "sample-league",
            "--snapshot-file",
            str(FIXTURE_SNAPSHOT),
            "--max-simulations",
            "100",
            "--batch-size",
            "50",
            "--plot",
            "off",
            "--no-gui",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 0

    manifests = list(tmp_path.glob("Sample_League/*/*/manifest.json"))
    assert manifests, "expected manifest.json to be created"
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["completed_simulations"] == 100
    assert manifest["league_name"] == "Sample League"
    assert len(manifest["position_probabilities"]) == 4


def test_simulate_snapshot_with_what_if(tmp_path):
    exit_code = cli.main(
        [
            "simulate",
            "sample-league",
            "--snapshot-file",
            str(FIXTURE_SNAPSHOT),
            "--set-result",
            "Alpha FC vs Bravo United=2-0",
            "--max-simulations",
            "50",
            "--batch-size",
            "25",
            "--plot",
            "off",
            "--no-gui",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 0

    manifests = list(tmp_path.glob("Sample_League/*/*/manifest.json"))
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    overrides = manifest["snapshot_metadata"]["what_if_results"]
    assert overrides[0]["fixture"] == "Alpha FC vs Bravo United"
    assert overrides[0]["score"] == "2-0"
