"""Exercise the rebuilt Rust boundary without automatic build assistance."""

import copy
import json
from pathlib import Path

import pytest

from league_outcome_simulator.simulation import simulate_season


SNAPSHOT = Path(__file__).parent / "fixtures" / "sample_snapshot.json"


def test_zero_scoring_season_has_zero_goals():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    tables = [
        copy.deepcopy(snapshot[key])
        for key in ("base_table", "home_table", "away_table")
    ]
    for table in tables:
        for row in table[1:]:
            row[1] = 1
            row[5] = row[6] = 0
    result = simulate_season(
        tables[0], snapshot["fixtures"], tables[1], tables[2],
        seed=42, auto_build=False,
    )
    assert all(stats["GF"] == stats["GA"] == 0 for _, stats in result)


def test_negative_scoring_rate_is_a_python_value_error():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for table_name in ("base_table", "home_table", "away_table"):
        for row in snapshot[table_name][1:]:
            row[1] = 1
            row[5] = -1
            row[6] = 1
    with pytest.raises(ValueError):
        simulate_season(
            snapshot["base_table"], snapshot["fixtures"],
            snapshot["home_table"], snapshot["away_table"],
            seed=42, auto_build=False,
        )
