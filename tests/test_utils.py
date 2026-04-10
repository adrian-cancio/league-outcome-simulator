from __future__ import annotations

from league_outcome_simulator.utils import (
    apply_result_to_split_tables,
    build_table_from_team_stats,
    choose_team_name,
    parse_score_string,
    team_stats_from_table,
)


def test_parse_score_string():
    assert parse_score_string("2-1") == (2, 1)
    assert parse_score_string(" 3 : 0 ") == (3, 0)


def test_choose_team_name_partial_match():
    candidates = ["Brighton & Hove Albion", "Chelsea"]
    assert choose_team_name("Brighton", candidates) == "Brighton & Hove Albion"


def test_apply_result_to_split_tables_and_build_table():
    base = team_stats_from_table(
        [
            ["Team", "M", "W", "D", "L", "G", "GA", "PTS"],
            ["Alpha", 0, 0, 0, 0, 0, 0, 0],
            ["Bravo", 0, 0, 0, 0, 0, 0, 0],
        ]
    )
    home = team_stats_from_table(
        [
            ["Team", "M", "W", "D", "L", "G", "GA", "PTS"],
            ["Alpha", 0, 0, 0, 0, 0, 0, 0],
            ["Bravo", 0, 0, 0, 0, 0, 0, 0],
        ]
    )
    away = team_stats_from_table(
        [
            ["Team", "M", "W", "D", "L", "G", "GA", "PTS"],
            ["Alpha", 0, 0, 0, 0, 0, 0, 0],
            ["Bravo", 0, 0, 0, 0, 0, 0, 0],
        ]
    )

    apply_result_to_split_tables(base, home, away, "Alpha", "Bravo", 2, 1)
    rebuilt = build_table_from_team_stats(base)
    assert rebuilt[1][0] == "Alpha"
    assert rebuilt[1][7] == 3
    assert home["Alpha"]["points"] == 3
    assert away["Bravo"]["losses"] == 1
