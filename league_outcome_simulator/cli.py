"""CLI for league outcome simulation, scenarios and backtesting."""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from tqdm import tqdm

from .catalog import LEAGUES_BY_INDEX, list_leagues, resolve_league_identifier
from .data import ReplaySnapshot, SofaScoreClient
from .error_estimation import calculate_pp_error
from .simulation import simulate_bulk
from .utils import (
    apply_result_to_split_tables,
    build_table_from_team_stats,
    choose_team_name,
    fixture_label,
    is_headless_environment,
    parse_score_string,
    process_team_colors,
    sanitize_path_segment,
    stable_seed_from_text,
    summarize_probability_matrix,
    team_stats_from_table,
    write_json,
)
from .visualization import print_simulation_results, visualize_results


LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_SIMULATIONS = 1_000_000
DEFAULT_MAX_TIME_SECONDS = 600
DEFAULT_TARGET_PP_ERROR = 0.01
DEFAULT_BATCH_SIZE = 50_000
DEFAULT_TOP_TABLES = 25
TopTableScores = dict[tuple[str, ...], float]


@dataclass(slots=True)
class SimulationConfig:
    league: str | None
    tournament_id: int | None
    season_id: int | None
    season_year: str | None
    max_simulations: int
    max_time_seconds: int
    target_pp_error: float
    batch_size: int
    seed: int | None
    top_tables: int
    output_dir: Path
    output_formats: tuple[str, ...]
    plot_mode: str
    no_gui: bool
    force_refresh: bool
    use_disk_cache: bool
    verbose: bool
    snapshot_file: Path | None
    what_if_results: tuple[str, ...]
    auto_build_rust: bool


def configure_logging(verbose: bool) -> None:
    """Configure root logging once."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def default_seed(config: SimulationConfig, league_name: str, season_id: int) -> int:
    """Generate a stable default seed when one was not provided."""
    if config.seed is not None:
        return config.seed
    return stable_seed_from_text(
        f"{league_name}|{season_id}|{config.max_simulations}|{config.target_pp_error}"
    )


def select_league_interactively() -> tuple[int, str]:
    """Legacy interactive selector for no-args usage."""
    print("Available leagues:")
    for league in list_leagues():
        print(f"{league.index}. {league.name} ({league.slug})")
    print("99. Enter custom tournament ID")

    try:
        choice = int(input("Select league number: ").strip())
    except ValueError:
        raise SystemExit("Invalid selection.")

    if choice == 99:
        custom_id = int(input("Enter SofaScore tournament ID: ").strip())
        return custom_id, f"Tournament {custom_id}"

    league = LEAGUES_BY_INDEX.get(choice)
    if league is None:
        raise SystemExit("Invalid selection.")
    return league.tournament_id, league.name


def resolve_tournament_and_name(config: SimulationConfig) -> tuple[int, str]:
    """Resolve tournament id and human-friendly league name."""
    if config.tournament_id is not None:
        league = resolve_league_identifier(config.tournament_id)
        return (
            config.tournament_id,
            league.name if league else f"Tournament {config.tournament_id}",
        )

    league = resolve_league_identifier(config.league)
    if league is not None:
        return league.tournament_id, league.name

    if config.league is not None and config.league.isdigit():
        tournament_id = int(config.league)
        return tournament_id, f"Tournament {tournament_id}"

    raise SystemExit("Provide a valid built-in league slug/name or --league-id.")


def load_live_snapshot(config: SimulationConfig) -> ReplaySnapshot:
    """Load a snapshot from SofaScore."""
    tournament_id, league_name = resolve_tournament_and_name(config)
    client = SofaScoreClient(
        use_disk_cache=config.use_disk_cache,
        force_refresh=config.force_refresh,
    )
    try:
        season_id = client.resolve_season_id(
            tournament_id,
            season_id=config.season_id,
            season_year=config.season_year,
        )
        if not season_id:
            raise SystemExit(
                f"Could not resolve season for tournament {tournament_id}."
            )

        base_table = client.get_league_table(tournament_id, season_id)
        home_table = client.get_home_league_table(tournament_id, season_id)
        away_table = client.get_away_league_table(tournament_id, season_id)
        fixtures = client.get_remaining_fixtures(tournament_id, season_id)
        team_colors = client.get_team_colors(tournament_id, season_id)
        metadata = client.get_season_metadata(tournament_id, season_id)
        metadata.update(
            {
                "snapshot_source": "live",
                "snapshot_timestamp": datetime.utcnow().isoformat() + "Z",
            }
        )

        if not base_table or not home_table or not away_table:
            raise SystemExit("Could not fetch standings data from SofaScore.")

        return ReplaySnapshot(
            tournament_id=tournament_id,
            season_id=season_id,
            league_name=league_name,
            snapshot_label="live",
            base_table=base_table,
            home_table=home_table,
            away_table=away_table,
            fixtures=fixtures,
            team_colors=team_colors,
            metadata=metadata,
        )
    finally:
        client.close()


def load_snapshot(config: SimulationConfig) -> ReplaySnapshot:
    """Load snapshot either from file or live SofaScore."""
    if config.snapshot_file:
        client = SofaScoreClient(
            use_disk_cache=config.use_disk_cache,
            force_refresh=config.force_refresh,
        )
        try:
            snapshot = client.load_snapshot(config.snapshot_file)
        finally:
            client.close()
        return snapshot
    return load_live_snapshot(config)


def apply_what_if_results(
    snapshot: ReplaySnapshot, expressions: tuple[str, ...]
) -> ReplaySnapshot:
    """Apply fixed pending results before simulating remaining fixtures."""
    if not expressions:
        return snapshot

    base_stats = team_stats_from_table(snapshot.base_table)
    home_stats = team_stats_from_table(snapshot.home_table)
    away_stats = team_stats_from_table(snapshot.away_table)
    remaining_fixtures = list(snapshot.fixtures)
    known_teams = list(base_stats)
    applied_overrides: list[dict[str, Any]] = []

    for expression in expressions:
        if "=" not in expression:
            raise SystemExit(
                f"Invalid what-if '{expression}'. Use 'Home Team vs Away Team=2-1'."
            )
        fixture_part, score_part = expression.split("=", 1)
        parts = re.split(r"\s+vs\s+", fixture_part, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) != 2:
            raise SystemExit(
                f"Invalid what-if '{expression}'. Use 'Home Team vs Away Team=2-1'."
            )
        left, right = parts
        home_team = choose_team_name(left.strip(), known_teams)
        away_team = choose_team_name(right.strip(), known_teams)
        home_goals, away_goals = parse_score_string(score_part)

        fixture_index = next(
            (
                index
                for index, fixture in enumerate(remaining_fixtures)
                if fixture["h"]["title"] == home_team
                and fixture["a"]["title"] == away_team
            ),
            None,
        )
        if fixture_index is None:
            raise SystemExit(
                f"Pending fixture not found: {fixture_label(home_team, away_team)}"
            )

        remaining_fixtures.pop(fixture_index)
        apply_result_to_split_tables(
            base_stats,
            home_stats,
            away_stats,
            home_team,
            away_team,
            home_goals,
            away_goals,
        )
        applied_overrides.append(
            {
                "fixture": fixture_label(home_team, away_team),
                "score": f"{home_goals}-{away_goals}",
            }
        )

    metadata = dict(snapshot.metadata)
    metadata["what_if_results"] = applied_overrides
    return ReplaySnapshot(
        tournament_id=snapshot.tournament_id,
        season_id=snapshot.season_id,
        league_name=snapshot.league_name,
        snapshot_label=snapshot.snapshot_label,
        base_table=build_table_from_team_stats(base_stats),
        home_table=build_table_from_team_stats(home_stats),
        away_table=build_table_from_team_stats(away_stats),
        fixtures=remaining_fixtures,
        team_colors=snapshot.team_colors,
        metadata=metadata,
    )


def build_run_directory(base_output_dir: Path, league_name: str) -> Path:
    """Create a run directory organized by league/date/time."""
    now = datetime.now()
    return (
        base_output_dir
        / sanitize_path_segment(league_name)
        / now.strftime("%Y-%m-%d")
        / now.strftime("%H-%M-%S")
    )


def should_show_plot(config: SimulationConfig) -> bool:
    """Decide whether to open a Matplotlib window."""
    if config.no_gui:
        return False
    if config.plot_mode == "off":
        return False
    if config.plot_mode == "on":
        return True
    return not is_headless_environment()


def run_simulation(config: SimulationConfig) -> dict[str, Any]:
    """Run the simulation end-to-end and return the run payload."""
    snapshot = apply_what_if_results(load_snapshot(config), config.what_if_results)
    if not snapshot.fixtures:
        raise SystemExit("No fixtures left to simulate after applying overrides.")

    run_dir = build_run_directory(config.output_dir, snapshot.league_name)
    run_dir.mkdir(parents=True, exist_ok=True)

    seed = default_seed(config, snapshot.league_name, snapshot.season_id)
    LOGGER.info(
        "Running %s simulations for %s season %s with seed %s",
        config.max_simulations,
        snapshot.league_name,
        snapshot.season_id,
        seed,
    )

    position_counts = {
        team: Counter() for team in [row[0] for row in snapshot.base_table[1:]]
    }
    top_tables_counter: TopTableScores = {}
    start_time = time.time()
    sim_count_completed = 0
    last_error_update_time = start_time
    stop_reason = "max_simulations"

    with tqdm(
        total=config.max_simulations,
        desc="Simulating seasons",
        unit="sim",
        leave=True,
    ) as progress:
        while sim_count_completed < config.max_simulations:
            elapsed = time.time() - start_time
            if elapsed > config.max_time_seconds:
                stop_reason = "time_limit"
                tqdm.write(
                    f"Maximum simulation time reached after {sim_count_completed} simulations."
                )
                break

            current_batch = min(
                config.batch_size, config.max_simulations - sim_count_completed
            )
            batch_seed = seed + sim_count_completed

            try:
                bulk_results = simulate_bulk(
                    snapshot.base_table,
                    snapshot.fixtures,
                    snapshot.home_table,
                    snapshot.away_table,
                    current_batch,
                    seed=batch_seed,
                    top_k_tables=config.top_tables,
                    auto_build=config.auto_build_rust,
                )
            except (
                Exception
            ) as exc:  # pragma: no cover - surfaced in CLI tests by smoke
                raise SystemExit(f"Error in bulk simulation: {exc}") from exc

            batch_positions = bulk_results["position_counts"]
            for team, pos_dict in batch_positions.items():
                for pos, count in pos_dict.items():
                    position_counts[team][int(pos)] += int(count)

            for table_entry in bulk_results.get("top_tables", []):
                batch_count = int(table_entry["count"])
                if batch_count <= 0:
                    continue
                weight = batch_count / current_batch
                table_key = tuple(str(team) for team in table_entry["table"])
                current_score = top_tables_counter.get(table_key, 0.0)
                top_tables_counter[table_key] = current_score + weight

            if len(top_tables_counter) > config.top_tables * 4:
                top_tables_counter = dict(
                    sorted(
                        top_tables_counter.items(),
                        key=lambda item: item[1],
                        reverse=True,
                    )[: config.top_tables]
                )

            sim_count_completed += current_batch
            progress.update(current_batch)

            current_time = time.time()
            if current_time - last_error_update_time >= 2 and sim_count_completed > 0:
                pp_error = calculate_pp_error(
                    position_counts,
                    sim_count_completed,
                    len(position_counts),
                )
                progress.set_postfix_str(f"Error: {pp_error:.3f} pp")
                if pp_error <= config.target_pp_error:
                    stop_reason = "target_pp_error"
                    tqdm.write(
                        f"Target PP Error of {config.target_pp_error:.3f} pp reached after {sim_count_completed} simulations."
                    )
                    break
                last_error_update_time = current_time

    elapsed_time = time.time() - start_time
    num_simulations = sim_count_completed
    final_pp_error = calculate_pp_error(
        position_counts,
        num_simulations,
        len(position_counts),
    )

    processed_colors = process_team_colors(snapshot.team_colors)
    print_simulation_results(
        position_counts,
        num_simulations,
        snapshot.base_table,
        Counter(top_tables_counter),
        run_dir,
        elapsed_time,
        len(snapshot.fixtures),
    )

    image_file = None
    if config.plot_mode != "off" and num_simulations > 0:
        image_file = visualize_results(
            position_counts,
            num_simulations,
            processed_colors,
            snapshot.base_table,
            run_dir,
            show_plot=should_show_plot(config),
        )

    manifest = {
        "league_name": snapshot.league_name,
        "tournament_id": snapshot.tournament_id,
        "season_id": snapshot.season_id,
        "seed": seed,
        "max_simulations": config.max_simulations,
        "completed_simulations": num_simulations,
        "max_time_seconds": config.max_time_seconds,
        "target_pp_error": config.target_pp_error,
        "final_pp_error": final_pp_error,
        "elapsed_time_seconds": elapsed_time,
        "stop_reason": stop_reason,
        "output_formats": list(config.output_formats),
        "snapshot_metadata": snapshot.metadata,
        "run_directory": str(run_dir),
        "image_file": str(image_file) if image_file else None,
        "position_probabilities": summarize_probability_matrix(
            position_counts, num_simulations
        ),
        "top_tables_note": (
            "Top tables are tracked approximately across batches using weighted batch frequency. "
            "They are useful for ranking candidate full tables, not as exact global probabilities."
        ),
        "top_tables": [
            {
                "table": list(table),
                "score": count,
            }
            for table, count in sorted(
                top_tables_counter.items(), key=lambda item: item[1], reverse=True
            )[: config.top_tables]
        ],
    }
    write_json(run_dir / "manifest.json", manifest)
    if "json" in config.output_formats:
        write_json(run_dir / "results.json", manifest)

    print(f"Completed {num_simulations} simulations")
    print(f"Final Average Percentage Point Error: {final_pp_error:.3f}")
    print(f"Run directory: {run_dir}")
    return manifest


def run_leagues_command() -> int:
    """Print available built-in leagues."""
    for league in list_leagues():
        print(
            f"{league.index:>2}  {league.slug:<24}  {league.tournament_id:<6}  {league.name}"
        )
    return 0


def run_backtest(
    config: SimulationConfig, matchday_cutoff: int, export_snapshot: bool
) -> int:
    """Run a basic backtest snapshot and then simulate from that point."""
    tournament_id, _ = resolve_tournament_and_name(config)
    client = SofaScoreClient(
        use_disk_cache=config.use_disk_cache,
        force_refresh=config.force_refresh,
    )
    try:
        season_id = client.resolve_season_id(
            tournament_id,
            season_id=config.season_id,
            season_year=config.season_year,
        )
        if not season_id:
            raise SystemExit("Could not resolve season for backtest.")
        snapshot = client.build_snapshot_from_cutoff(
            tournament_id,
            season_id,
            matchday_cutoff=matchday_cutoff,
        )
        final_table = client.get_league_table(tournament_id, season_id)
        if final_table is None:
            raise SystemExit("Could not load final standings for backtest evaluation.")
        if export_snapshot:
            snapshot_path = (
                config.output_dir
                / "snapshots"
                / sanitize_path_segment(snapshot.league_name)
                / f"{snapshot.snapshot_label}.json"
            )
            client.export_snapshot(snapshot, snapshot_path)
            print(f"Saved snapshot to {snapshot_path}")
    finally:
        client.close()

    run_config = replace(config, snapshot_file=None)
    original_loader = load_snapshot

    def load_backtest_snapshot(_: SimulationConfig) -> ReplaySnapshot:
        return snapshot

    globals()["load_snapshot"] = load_backtest_snapshot
    try:
        manifest = run_simulation(run_config)
    finally:
        globals()["load_snapshot"] = original_loader

    expected_positions = {
        row[0]: position for position, row in enumerate(final_table[1:], start=1)
    }
    simulated_summary = {
        entry["team"]: entry["positions"]
        for entry in manifest["position_probabilities"]
    }
    mean_expected_position = {
        team: sum(int(pos) * probability for pos, probability in positions.items())
        for team, positions in simulated_summary.items()
    }
    average_abs_position_error = sum(
        abs(mean_expected_position[team] - expected_positions[team])
        for team in expected_positions
    ) / max(1, len(expected_positions))
    backtest_report = {
        "league_name": snapshot.league_name,
        "season_id": snapshot.season_id,
        "snapshot_label": snapshot.snapshot_label,
        "completed_simulations": manifest["completed_simulations"],
        "stop_reason": manifest["stop_reason"],
        "average_abs_position_error": average_abs_position_error,
        "actual_final_positions": expected_positions,
        "expected_positions_from_simulation": mean_expected_position,
    }
    write_json(Path(manifest["run_directory"]) / "backtest.json", backtest_report)

    print(json.dumps(backtest_report, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argparse parser."""
    parser = argparse.ArgumentParser(prog="league_outcome_simulator")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command")

    leagues_parser = subparsers.add_parser("leagues", help="List built-in leagues")
    leagues_parser.set_defaults(command="leagues")

    simulate_parser = subparsers.add_parser("simulate", help="Run a simulation")
    simulate_parser.add_argument(
        "league", nargs="?", help="League slug/name or tournament id"
    )
    simulate_parser.add_argument("--league-id", type=int, dest="tournament_id")
    simulate_parser.add_argument("--season-id", type=int)
    simulate_parser.add_argument("--season-year")
    simulate_parser.add_argument(
        "--max-simulations", type=int, default=DEFAULT_MAX_SIMULATIONS
    )
    simulate_parser.add_argument(
        "--max-time", type=int, default=DEFAULT_MAX_TIME_SECONDS
    )
    simulate_parser.add_argument(
        "--target-pp-error", type=float, default=DEFAULT_TARGET_PP_ERROR
    )
    simulate_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    simulate_parser.add_argument("--seed", type=int)
    simulate_parser.add_argument("--top-tables", type=int, default=DEFAULT_TOP_TABLES)
    simulate_parser.add_argument("--output-dir", type=Path, default=Path("results"))
    simulate_parser.add_argument(
        "--format",
        dest="output_formats",
        action="append",
        choices=("txt", "json", "png"),
        help="Repeat to add output formats. Defaults to txt+json+png.",
    )
    simulate_parser.add_argument(
        "--plot",
        choices=("auto", "on", "off"),
        default="auto",
        help="Whether to render or save charts.",
    )
    simulate_parser.add_argument("--no-gui", action="store_true")
    simulate_parser.add_argument("--force-refresh", action="store_true")
    simulate_parser.add_argument("--no-disk-cache", action="store_true")
    simulate_parser.add_argument("--snapshot-file", type=Path)
    simulate_parser.add_argument(
        "--set-result",
        dest="what_if_results",
        action="append",
        default=[],
        help="Override a pending fixture, e.g. 'Arsenal vs Chelsea=2-1'.",
    )
    simulate_parser.add_argument(
        "--no-auto-build-rust",
        action="store_true",
        help="Disable automatic Rust build assistance when the extension is missing.",
    )

    backtest_parser = subparsers.add_parser(
        "backtest", help="Run a historical replay backtest"
    )
    backtest_parser.add_argument("league", help="League slug/name or tournament id")
    backtest_parser.add_argument("--league-id", type=int, dest="tournament_id")
    backtest_parser.add_argument("--season-id", type=int)
    backtest_parser.add_argument("--season-year")
    backtest_parser.add_argument("--matchday-cutoff", type=int, required=True)
    backtest_parser.add_argument("--max-simulations", type=int, default=250_000)
    backtest_parser.add_argument("--max-time", type=int, default=300)
    backtest_parser.add_argument(
        "--target-pp-error", type=float, default=DEFAULT_TARGET_PP_ERROR
    )
    backtest_parser.add_argument("--batch-size", type=int, default=25_000)
    backtest_parser.add_argument("--seed", type=int)
    backtest_parser.add_argument("--top-tables", type=int, default=DEFAULT_TOP_TABLES)
    backtest_parser.add_argument("--output-dir", type=Path, default=Path("results"))
    backtest_parser.add_argument("--plot", choices=("auto", "on", "off"), default="off")
    backtest_parser.add_argument("--no-gui", action="store_true")
    backtest_parser.add_argument("--no-disk-cache", action="store_true")
    backtest_parser.add_argument("--export-snapshot", action="store_true")
    backtest_parser.add_argument(
        "--no-auto-build-rust",
        action="store_true",
        help="Disable automatic Rust build assistance when the extension is missing.",
    )
    backtest_parser.set_defaults(output_formats=["txt", "json"])

    return parser


def config_from_args(args: argparse.Namespace) -> SimulationConfig:
    """Translate parsed args into the runtime config dataclass."""
    output_formats = tuple(args.output_formats or ["txt", "json", "png"])
    return SimulationConfig(
        league=getattr(args, "league", None),
        tournament_id=getattr(args, "tournament_id", None),
        season_id=getattr(args, "season_id", None),
        season_year=getattr(args, "season_year", None),
        max_simulations=getattr(args, "max_simulations", DEFAULT_MAX_SIMULATIONS),
        max_time_seconds=getattr(args, "max_time", DEFAULT_MAX_TIME_SECONDS),
        target_pp_error=getattr(args, "target_pp_error", DEFAULT_TARGET_PP_ERROR),
        batch_size=getattr(args, "batch_size", DEFAULT_BATCH_SIZE),
        seed=getattr(args, "seed", None),
        top_tables=getattr(args, "top_tables", DEFAULT_TOP_TABLES),
        output_dir=getattr(args, "output_dir", Path("results")),
        output_formats=output_formats,
        plot_mode=getattr(args, "plot", "auto"),
        no_gui=getattr(args, "no_gui", False),
        force_refresh=getattr(args, "force_refresh", False),
        use_disk_cache=not getattr(args, "no_disk_cache", False),
        verbose=getattr(args, "verbose", False),
        snapshot_file=getattr(args, "snapshot_file", None),
        what_if_results=tuple(getattr(args, "what_if_results", [])),
        auto_build_rust=not getattr(args, "no_auto_build_rust", False),
    )


def run_legacy_interactive() -> int:
    """Preserve the original no-args experience."""
    tournament_id, league_name = select_league_interactively()
    config = SimulationConfig(
        league=league_name,
        tournament_id=tournament_id,
        season_id=None,
        season_year=None,
        max_simulations=DEFAULT_MAX_SIMULATIONS,
        max_time_seconds=DEFAULT_MAX_TIME_SECONDS,
        target_pp_error=DEFAULT_TARGET_PP_ERROR,
        batch_size=DEFAULT_BATCH_SIZE,
        seed=None,
        top_tables=DEFAULT_TOP_TABLES,
        output_dir=Path("results"),
        output_formats=("txt", "json", "png"),
        plot_mode="auto",
        no_gui=False,
        force_refresh=False,
        use_disk_cache=True,
        verbose=False,
        snapshot_file=None,
        what_if_results=(),
        auto_build_rust=True,
    )
    run_simulation(config)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(getattr(args, "verbose", False))

    if args.command is None:
        return run_legacy_interactive()

    if args.command == "leagues":
        return run_leagues_command()

    config = config_from_args(args)
    if args.command == "simulate":
        run_simulation(config)
        return 0
    if args.command == "backtest":
        return run_backtest(config, args.matchday_cutoff, args.export_snapshot)

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
