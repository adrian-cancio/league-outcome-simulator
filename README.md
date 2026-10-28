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

## Data Source

Data comes from SofaScore's JSON API over HTTP (`curl_cffi`, no browser automation, no proxies).

- **Host and TLS profile.** `api.sofascore.com` now answers HTTP 403 (`"challenge"`) to Chrome-style clients. The default is `https://www.sofascore.com/api/v1` with the `safari17_0` impersonation profile, which serves the same JSON.
- **Configurable.** `--base-url` / `--impersonate` (on `simulate` and `backtest`), the `SOFASCORE_BASE_URL` / `SOFASCORE_IMPERSONATE` environment variables, or the `SofaScoreClient(base_url=..., impersonate=...)` arguments. Order of precedence: argument/flag, environment, default.
- **Fallback.** On HTTP 403 the client tries the other host and the other profiles (`safari18_0`, `safari15_5`, `chrome124`) before giving up, and remembers what worked. A 403 is treated as a block, not retried. If everything is blocked the run stops with an error that names the 403 challenge and suggests `--snapshot-file`.
- **Offline.** `--snapshot-file` simulates from a saved snapshot (see `tests/fixtures/sample_snapshot.json`) and never touches the network.

## Disk Cache

Responses are cached in `.cache/sofascore/` as `v2-<hash>.json` files holding the URL, a fetched-at timestamp and the payload. The key is the API path (it contains the season id), so a new season can never reuse an old season's entries.

| Data | Lifetime |
| --- | --- |
| Season list | 1 day |
| Standings / fixtures of the current (unfinished) season | 30 minutes |
| Season that finished more than 2 days ago | 30 days |
| A season that finished less than 2 days ago | 30 minutes (the final table can still be corrected) |

- Cache files from older versions (`<number>.json`, no metadata) are **never used** and never deleted; the run logs that they are ignored.
- Expired, unreadable or future-dated entries are refetched.
- `--force-refresh` skips the cache for the whole run (and rewrites it). `--no-disk-cache` neither reads nor writes it.
- Every run logs where each piece of data came from, e.g. `Data from cache: /unique-tournament/54/seasons (fetched 2026-10-28T00:24:25Z, age 9s)`, plus one summary line (`Data source: network|cache|mixed ...`). `simulation.txt` starts with `Season:` and `Data source:` lines, and `manifest.json` / `results.json` record `season_name`, `season_id`, `data_source` (`network`, `cache`, `mixed` or `snapshot`), `fetched_at` (the oldest input) and `data_age_seconds`.

## Season Selection

With no `--season-id` / `--season-year`, the simulator inspects the three newest listed seasons, newest first, and applies this rule:

1. The first season that is **in progress** (some matches played and fixtures remaining) is used. Newer seasons that have not started are skipped with a warning.
2. If no season is in progress but the newest has **not started** and has fixtures scheduled, that season is simulated from an empty table, with a warning.
3. If the newest season is **finished** (0 remaining fixtures) and nothing else qualifies, the run stops and tells you to pass `--season-year` (for example `25/26`) or `--season-id`. It never silently simulates an old season.

An explicitly requested season is honoured, with a warning if it is finished or has not started (a finished season then ends with "No fixtures left to simulate"). An unknown `--season-id` or `--season-year` stops with an error that lists the newest available seasons.

Details of the rule:

- **Live, postponed or interrupted matches** (anything unplayed that is not simply scheduled) keep a season "in progress". They are never simulated and never given an invented score; the run warns about them. If only such matches remain, the run stops and says so. A match postponed more than 14 days ago no longer counts.
- **No standings published** (HTTP 404 or an empty table) is treated as a season that has not started only when no match has been played: everyone then starts on zero, with the teams taken from the fixture list. Otherwise the run fails instead of simulating from zeros.
- **`backtest` without `--season-year`/`--season-id`** replays the newest of the three newest seasons that has played matches (a finished season is ideal; an in-progress one is used with a warning). This is deliberately different from the prediction rule above, which wants fixtures left to simulate.
- `data_source`, `fetched_at` and the request counts in the manifest describe only the season that was used, not seasons that were merely inspected while choosing.

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

- tournament and season ids, season name
- data source (`network` / `cache` / `mixed` / `snapshot`), fetched-at time and age
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
python -m pytest -q
cargo test --release
```

The Python tests never touch the network: HTTP is mocked and the CLI is exercised end to end from `tests/fixtures/sample_snapshot.json` through the Rust extension (build it first with `python -m maturin develop --release`).

One opt-in smoke test talks to the real SofaScore API (fetches `la-liga-2` and runs a short simulation in a temporary cache). It is skipped unless you ask for it:

```bash
LOS_LIVE_TESTS=1 python -m pytest -m live -q
```

Rebuild the Rust extension manually if needed:

```bash
python -m maturin develop --release
```

## License

[MIT](LICENSE)
