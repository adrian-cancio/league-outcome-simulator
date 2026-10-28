"""Frozen-origin evaluation of the unchanged Rust score kernel.

Input is a JSON object with an ``events`` array of SofaScore event records.
No network or cache writes are performed by this command.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from .simulation import get_rust_module
from .utils import (
    TEAM_ROW_HEADER, apply_result_to_split_tables,
    build_table_from_team_stats, team_stats_from_table,
)

COMPLETION_SECONDS = 3 * 60 * 60
SIMPLEX_TOLERANCE = 1e-10
LOG_FLOOR = 1e-15


def parse_origin(value: str) -> float:
    """Require an explicitly UTC ISO timestamp, never local time."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("Origin must have an explicit UTC offset (Z or +00:00)")
    return parsed.timestamp()


def validate_probabilities(probabilities):
    if (len(probabilities) != 3
            or any(not math.isfinite(p) or p < 0 or p > 1 for p in probabilities)
            or abs(sum(probabilities) - 1) > SIMPLEX_TOLERANCE):
        raise ValueError(f"Invalid H/D/A simplex: {probabilities}")


def score(probabilities, observed):
    """Sum-three Brier; natural log with an explicit observed-class floor."""
    validate_probabilities(probabilities)
    return {
        "brier": sum((p - int(i == observed)) ** 2
                     for i, p in enumerate(probabilities)),
        "log_loss": -math.log(max(probabilities[observed], LOG_FLOOR)),
        "log_floor_applied": probabilities[observed] < LOG_FLOOR,
    }


def evaluate(events, origin: str, test_end=None, candidate=False):
    """Fit once from pre-origin results; never update on held-out outcomes."""
    boundary = parse_origin(origin)
    end = parse_origin(test_end) if test_end is not None else math.inf
    if end <= boundary:
        raise ValueError("test_end must be strictly after origin")
    train, test, exclusions = [], [], []
    seen = set()
    normalized_availability = {}
    for event in events:
        identifier = event.get("id")
        if type(identifier) is not int or identifier <= 0:
            raise ValueError("Each event ID must be a positive integer (excluding bool)")
        if identifier in seen:
            raise ValueError(f"Duplicate event ID: {identifier}")
        seen.add(identifier)
        reason = None
        kickoff = event.get("startTimestamp")
        availability = event.get("resultAvailableTimestamp")
        source = "explicit" if availability is not None else "kickoff_plus_3h"
        if availability is None and isinstance(kickoff, (int, float)):
            availability = kickoff + COMPLETION_SECONDS
        normalized_availability[identifier] = {
            "id": identifier, "kickoff": kickoff,
            "available_at": availability, "source": source,
        }
        goals = [event.get(key, {}).get("current")
                 for key in ("homeScore", "awayScore")]
        if event.get("status", {}).get("type") != "finished":
            reason = "unresolved"
        elif any(type(g) is not int or g < 0 for g in goals):
            reason = "missing_or_invalid_score"
        elif (isinstance(kickoff, bool) or not isinstance(kickoff, (int, float))
              or not math.isfinite(kickoff)):
            reason = "missing_or_invalid_kickoff"
        elif not all(event.get(key, {}).get("name") for key in ("homeTeam", "awayTeam")):
            reason = "missing_team"
        elif (isinstance(availability, bool) or not isinstance(availability, (int, float))
              or not math.isfinite(availability) or availability < kickoff):
            reason = "invalid_result_availability"
        elif availability < boundary:
            train.append(event)
        elif kickoff >= end:
            reason = "outside_test_window"
        elif kickoff >= boundary:
            test.append(event)
        else:
            reason = "result_not_available_before_origin"
        if reason:
            exclusions.append({"id": identifier, "reason": reason})
    if not train or not test:
        raise ValueError("Evaluation requires nonempty resolved training and test sets")
    train.sort(key=lambda e: (e["startTimestamp"], str(e["id"])))
    test.sort(key=lambda e: (e["startTimestamp"], str(e["id"])))
    teams = sorted({e[key]["name"] for e in train for key in ("homeTeam", "awayTeam")})
    tables = [team_stats_from_table([TEAM_ROW_HEADER.copy(),
              *[[name, 0, 0, 0, 0, 0, 0, 0] for name in teams]]) for _ in range(3)]
    counts = [1, 1, 1]
    for event in train:
        home, away = event["homeScore"]["current"], event["awayScore"]["current"]
        counts[0 if home > away else 1 if home == away else 2] += 1
        apply_result_to_split_tables(*tables, event["homeTeam"]["name"],
                                     event["awayTeam"]["name"], home, away)
    eligible = []
    for event in test:
        if any(event[key]["name"] not in teams for key in ("homeTeam", "awayTeam")):
            exclusions.append({"id": event["id"], "reason": "team_absent_from_training"})
        else:
            eligible.append(event)
    test = eligible
    if not test:
        raise ValueError("Evaluation has no test fixtures with training-known teams")
    fixtures = [{"h": {"title": e["homeTeam"]["name"]},
                 "a": {"title": e["awayTeam"]["name"]}} for e in test]
    rust = get_rust_module(auto_build=False)
    base, home_table, away_table = map(build_table_from_team_stats, tables)
    predictions = rust.predict_outcomes(base, fixtures, home_table, away_table)
    if len(predictions) != len(test):
        raise ValueError("Rust prediction count does not match test fixtures")
    candidate_predictions = (rust.predict_outcomes(
        base, fixtures, home_table, away_table, "venue_normalized_v1")
        if candidate else None)
    if candidate and len(candidate_predictions) != len(test):
        raise ValueError("Candidate prediction count does not match fixtures")
    empirical = [count / sum(counts) for count in counts]
    rows = []
    for event, prediction in zip(test, predictions):
        home, away = event["homeScore"]["current"], event["awayScore"]["current"]
        observed = 0 if home > away else 1 if home == away else 2
        probabilities = {"model": prediction, "uniform": [1 / 3] * 3,
                         "empirical": empirical}
        if candidate:
            probabilities["candidate"] = candidate_predictions[len(rows)]
        rows.append({"id": event["id"], "kickoff": event["startTimestamp"],
                     "home": event["homeTeam"]["name"], "away": event["awayTeam"]["name"],
                     "score": [home, away], "observed": "HDA"[observed],
                     "probabilities": probabilities,
                     "scores": {name: score(p, observed) for name, p in probabilities.items()}})
    metrics = {name: {metric: sum(row["scores"][name][metric] for row in rows) / len(rows)
                      for metric in ("brier", "log_loss")}
               for name in rows[0]["probabilities"]}
    return {
        "schema": 1, "origin": datetime.fromtimestamp(boundary, timezone.utc).isoformat(),
        "test_end_exclusive": test_end,
        "model_ids": {"model": "current_v1", **({"candidate": "venue_normalized_v1"} if candidate else {})},
        "log_floor_counts": {name: sum(row["scores"][name]["log_floor_applied"] for row in rows) for name in metrics},
        "evidence": "preliminary single frozen-origin block; not generalization evidence",
        "availability_assumption": "Final status and scores from retrospective records; resultAvailableTimestamp when supplied, otherwise kickoff + 3 hours, strictly before origin. The approximation is not verified publication time and may be optimistic for interrupted matches or later corrections.",
        "completion_seconds": COMPLETION_SECONDS,
        "model_constants": dict(zip(("home_advantage_fallback", "default_lambda", "rho", "max_goals"), rust.MODEL_CONSTANTS)),
        "conventions": {"classes": ["H", "D", "A"], "brier": "mean sum of three squared errors",
                        "log_loss": "mean negative natural log of max(observed probability, floor)",
                        "log_floor": LOG_FLOOR, "simplex_tolerance": SIMPLEX_TOLERANCE,
                        "empirical_laplace_alpha": 1, "invalid_probabilities": "abort; no partial scoring"},
        "train_ids": [e["id"] for e in train], "test_ids": [e["id"] for e in test],
        "train_count": len(train), "test_count": len(test), "exclusions": exclusions,
        "training_availability": [normalized_availability[e["id"]] for e in train],
        "training_tables": {"total": base, "home": home_table, "away": away_table},
        "empirical_probabilities": empirical, "metrics": metrics, "fixtures": rows,
        "invalid_probability_count": 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events-file", type=Path, required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--test-end")
    parser.add_argument("--candidate", action="store_true")
    args = parser.parse_args(argv)
    try:
        raw = args.events_file.read_bytes()
        report = evaluate(json.loads(raw)["events"], args.origin,
                          args.test_end, args.candidate)
        report["input"] = {"path": str(args.events_file.resolve()),
                           "sha256": hashlib.sha256(raw).hexdigest()}
        args.output_dir.mkdir(parents=True, exist_ok=True)
        destination = args.output_dir / "evaluation.json"
        with destination.open("x", encoding="utf-8") as output:
            output.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        parser.exit(2, f"Evaluation failed: {exc}\n")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
