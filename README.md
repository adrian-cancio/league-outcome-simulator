# League Outcome Simulator

Rust-accelerated football league simulator that estimates the probability of every final table position from live SofaScore data or replayable snapshots.

## What Changed

- Non-interactive CLI with direct league selection.
- `--no-gui` and `--plot off` for headless runs.
- HTTP data fetching via `curl_cffi` instead of Selenium in the normal path.
- Reproducible runs with seed, JSON manifest and saved artifacts.
- Approximate ranked top full final tables.
- `what-if` scenario overrides for pending fixtures.
- First backtesting workflow with historical cutoffs.
- Python and Rust tests plus CI.

## Install

Requirements:

- Python 3.10+
- Rust toolchain for local editable installs and development

Recommended setup:

```bash
python -m venv venv
source venv/bin/activate
pip install -e .
```

On Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e .
```

If the Rust extension is not available yet, the CLI can attempt a local build automatically the first time you run a simulation.

## Quick Start

Interactive legacy mode:

```bash
python -m league_outcome_simulator
```

Non-interactive mode:

```bash
python -m league_outcome_simulator simulate premier-league --no-gui --plot off
```

List supported built-in leagues:

```bash
python -m league_outcome_simulator leagues
```

Use a custom SofaScore tournament id:

```bash
python -m league_outcome_simulator simulate --league-id 17 --no-gui --plot off
```

## CLI Examples

Run a bounded simulation:

```bash
python -m league_outcome_simulator simulate la-liga \
  --max-simulations 200000 \
  --max-time 120 \
  --target-pp-error 0.02 \
  --no-gui \
  --plot off
```

Replay from a saved snapshot:

```bash
python -m league_outcome_simulator simulate sample-league \
  --snapshot-file tests/fixtures/sample_snapshot.json \
  --max-simulations 1000 \
  --no-gui \
  --plot off
```

Run a what-if scenario:

```bash
python -m league_outcome_simulator simulate premier-league \
  --set-result "Arsenal vs Chelsea=2-1" \
  --no-gui \
  --plot off
```

Run a backtest from a historical matchday cutoff:

```bash
python -m league_outcome_simulator backtest premier-league \
  --season-year 24/25 \
  --matchday-cutoff 20 \
  --max-simulations 50000 \
  --plot off \
  --no-gui
```

## Outputs

Each run creates a directory under `results/<league>/<date>/<time>/` with:

- `simulation.txt`: human-readable summary
- `manifest.json`: reproducible run metadata
- `results.json`: machine-readable summary when JSON output is enabled
- `probabilities.png`: chart when plotting is enabled
- `backtest.json`: only for backtests

The manifest includes:

- tournament and season ids
- seed
- stop reason
- elapsed time
- Monte Carlo error estimate
- ranked top full tables
- scenario overrides applied to fixtures

## How It Works

1. `data.py`
   Loads standings, fixtures and historical events from SofaScore over HTTP and can export replay snapshots.

2. `src/lib.rs`
   Runs the season simulation in Rust with a Dixon-Coles style score model and bulk Monte Carlo aggregation.

3. `cli.py`
   Handles argument parsing, stop conditions, what-if overrides, manifests and backtests.

4. `visualization.py`
   Saves a stacked probability chart and can avoid GUI windows entirely.

## Notes And Limits

- The Monte Carlo PP error is a sampling stability metric, not proof that the model is accurate in the real world.
- Ranked top full tables are tracked approximately across batches. They are useful for ranking candidate final tables, not as exact global probabilities.
- Tie-breakers currently follow points, goal difference, goals scored, then team name.
- Some leagues have custom tie-breakers or asymmetric calendars; probability outputs remain useful, but league-specific rules are not yet fully modeled.

## Development

Install dev dependencies and run tests:

```bash
pip install -e .[dev]
pytest
cargo test --release
```

Rebuild the Rust extension manually if needed:

```bash
python -m maturin develop --release
```

## License

[MIT](LICENSE)
