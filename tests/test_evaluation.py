"""Chronological leakage and proper-score regression tests."""
import copy
import math

import pytest

from league_outcome_simulator.evaluation import evaluate, parse_origin, score

ORIGIN = "2024-01-01T00:00:00Z"
BOUNDARY = parse_origin(ORIGIN)


def event(identifier, offset, goals=(1, 1), status="finished", round_number=1):
    return {"id": identifier, "startTimestamp": BOUNDARY + offset,
            "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"},
            "homeScore": {"current": goals[0]}, "awayScore": {"current": goals[1]},
            "status": {"type": status}, "roundInfo": {"round": round_number}}


def test_frozen_predictions_and_postponed_early_round():
    events = [event(1, -86400), event(2, 86400, round_number=1),
              event(3, 172800, round_number=20), event(4, -3600),
              event(5, 100, status="postponed")]
    original = evaluate(events, ORIGIN)
    changed = copy.deepcopy(events)
    changed[1]["homeScore"]["current"] = 9
    changed[2]["awayScore"]["current"] = 7
    alternate = evaluate(changed, ORIGIN)
    assert original["train_ids"] == [1]
    assert original["test_ids"] == [2, 3]
    assert original["training_tables"] == alternate["training_tables"]
    assert original["empirical_probabilities"] == alternate["empirical_probabilities"]
    assert [r["probabilities"] for r in original["fixtures"]] == [
        r["probabilities"] for r in alternate["fixtures"]]
    assert original["exclusions"] == [
        {"id": 4, "reason": "result_not_available_before_origin"},
        {"id": 5, "reason": "unresolved"}]
    for row in original["fixtures"]:
        assert set(row["scores"]) == {"model", "uniform", "empirical"}


def test_metrics_and_explicit_zero_policy():
    result = score([0.5, 0.25, 0.25], 0)
    assert result["brier"] == pytest.approx(0.375)
    assert result["log_loss"] == pytest.approx(math.log(2))
    assert score([0, 1, 0], 0)["log_loss"] == pytest.approx(-math.log(1e-15))
    assert score([0, 1, 0], 0)["log_floor_applied"] is True


@pytest.mark.parametrize("probabilities", [[float("nan"), 0, 1], [0.2] * 3,
                                           [-0.1, 0.5, 0.6]])
def test_invalid_simplex(probabilities):
    with pytest.raises(ValueError, match="simplex"):
        score(probabilities, 0)


@pytest.mark.parametrize("events", [[], [event(1, -86400)], [event(1, 86400)]])
def test_empty_sets(events):
    with pytest.raises(ValueError, match="nonempty"):
        evaluate(events, ORIGIN)


def test_strict_availability_and_missing_scores():
    missing = event(4, -86400)
    missing["homeScore"] = {}
    report = evaluate([event(1, -86400), event(2, 0),
                       event(3, -10800), missing], ORIGIN)
    assert report["train_ids"] == [1]
    assert report["test_ids"] == [2]
    assert {e["reason"] for e in report["exclusions"]} == {
        "result_not_available_before_origin", "missing_or_invalid_score"}


def test_explicit_availability_overrides_kickoff_approximation():
    delayed = event(3, -86400)
    delayed["resultAvailableTimestamp"] = BOUNDARY + 1
    report = evaluate([event(1, -172800), event(2, 100), delayed], ORIGIN)
    assert report["train_ids"] == [1]
    assert {"id": 3, "reason": "result_not_available_before_origin"} in report["exclusions"]


def test_cli_auditable_json_and_no_overwrite(tmp_path):
    import json
    from league_outcome_simulator.evaluation import main

    source = tmp_path / "events.json"
    source.write_text(json.dumps({"events": [event(1, -86400), event(2, 100)]}))
    args = ["--events-file", str(source), "--origin", ORIGIN,
            "--output-dir", str(tmp_path / "out")]
    assert main(args) == 0
    report = json.loads((tmp_path / "out/evaluation.json").read_text())
    assert len(report["input"]["sha256"]) == 64
    assert report["training_availability"][0]["available_at"] < BOUNDARY
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


@pytest.mark.parametrize("explicit", [False, True])
def test_normalized_availability_is_shared_with_split(explicit):
    training = event(1, -86400)
    boundary = event(3, -10800)
    training["resultAvailableTimestamp"] = BOUNDARY - 100 if explicit else None
    boundary["resultAvailableTimestamp"] = None
    original = copy.deepcopy([training, event(2, 100), boundary])
    report = evaluate(original, ORIGIN)
    assert report["train_ids"] == [1]
    assert report["training_availability"] == [{
        "id": 1, "kickoff": training["startTimestamp"],
        "available_at": BOUNDARY - 100 if explicit else BOUNDARY - 75600,
        "source": "explicit" if explicit else "kickoff_plus_3h",
    }]
    assert {"id": 3, "reason": "result_not_available_before_origin"} in report["exclusions"]
    assert original == [training, event(2, 100), boundary]


@pytest.mark.parametrize("identifier", [None, True, False, 0, -1, "1", 1.0, [], {}])
def test_event_ids_require_positive_integer(identifier):
    with pytest.raises(ValueError, match="positive integer"):
        evaluate([event(1, -86400), event(identifier, 100)], ORIGIN)


def test_duplicate_event_id_is_clear_error():
    with pytest.raises(ValueError, match="Duplicate event ID: 1"):
        evaluate([event(1, -86400), event(1, 100)], ORIGIN)


def test_direct_backtest_refuses_without_accessing_config():
    from league_outcome_simulator.cli import run_backtest

    with pytest.raises(ValueError, match="deprecated.*explicit UTC"):
        run_backtest(None, 1, False)


def test_utc_required():
    with pytest.raises(ValueError, match="UTC"):
        parse_origin("2024-01-01T00:00:00")
