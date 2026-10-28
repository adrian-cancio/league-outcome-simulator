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

- Cache files from older versions (`<number>.json`, no metadata) are **never used** or automatically deleted; the run logs that they are ignored. To list them without deleting anything, run `python -m league_outcome_simulator cache clean --dry-run`. Omit `--dry-run` to explicitly remove legacy, non-`v2-` regular `*.json` files. `--cache-dir <path>` overrides the default `.cache/sofascore/`; v2 entries, temporary files, symlinks and subdirectories are preserved.
- Cache writes exclusively reserve temporary files (process id plus a random suffix), retry name collisions up to ten times, and publish by atomic replacement. Failed writes warn and leave existing published entries intact.
- Expired, unreadable, nonfinite-timestamp or future-dated entries are refetched. Cached season states with unknown statuses, invalid boolean/count types or negative counts are recomputed.
- `--force-refresh` skips the cache for the whole run (and rewrites it). `--no-disk-cache` neither reads nor writes it.
- Every run logs where each piece of data came from, e.g. `Data from cache: /unique-tournament/54/seasons (fetched 2026-10-28T00:24:25Z, age 9s)`, plus one summary line (`Data source: network|cache|mixed ...`). `simulation.txt` starts with `Season:` and `Data source:` lines, and `manifest.json` / `results.json` record `season_name`, `season_id`, `data_source` (`network`, `cache`, `mixed` or `snapshot`), `fetched_at` (the oldest input) and `data_age_seconds`.

The Rust score kernel rejects invalid/nonfinite rates, negative Dixon–Coles cell masses, and distributions without finite positive mass rather than clamping cells or fabricating outcomes. A zero Poisson rate assigns probability one to zero goals. This correctness hardening does not calibrate the model: venue normalization and constants remain unchanged and predictive validation is still pending.

### Development-only venue-normalized candidate

The default remains `current_v1`. The deterministic Rust predictor also accepts
`venue_normalized_v1` explicitly; season simulation is unchanged. Both use the
same Dixon–Coles distribution kernel, rho and score truncation. With training
venue means `H = sum(home GF)/sum(home played)` and
`A = sum(away GF)/sum(away played)`, candidate rates are
`H*(home GF/home played/H)*(away GA/away played/H)` and
`A*(away GF/away played/A)*(home GA/home played/A)`. There is no additional
home multiplier or tuned shrinkage. Zero league venue means or absent fixture
venue samples abort rather than inventing rates.

Offline evaluation supports `--candidate` and an explicitly UTC `--test-end`
(exclusive), alongside `--origin`; reports label model versions and include
identical-fixture baselines, exclusions and log-floor counts. Invalid probability
simplexes abort the evaluation. This is a development experiment, not a
confirmed superiority claim; no reserved confirmation outcomes are required.

## Season Selection

With no `--season-id` / `--season-year`, the simulator inspects the three newest listed seasons, newest first, and applies this rule:

1. The first season that is **in progress** (some matches played and fixtures remaining) is used. Newer seasons that have not started are skipped with a warning.
2. If no season is in progress but the newest has **not started** and has fixtures scheduled, that season is simulated from an empty table, with a warning.
3. If the newest season is **finished** (0 remaining fixtures) and nothing else qualifies, the run stops and tells you to pass `--season-year` (for example `25/26`) or `--season-id`. It never silently simulates an old season.

An explicitly requested season is honoured, with a warning if it is finished or has not started (a finished season then ends with "No fixtures left to simulate"). An unknown `--season-id` or `--season-year` stops with an error that lists the newest available seasons.

Details of the rule:

- **Live, postponed or interrupted matches** (anything unplayed that is not simply scheduled) keep a season "in progress". They are never simulated and never given an invented score; the run warns about them. If only such matches remain, the run stops and says so. A match postponed more than 14 days ago no longer counts.
- **No standings published** (HTTP 404 or an empty table) is treated as a season that has not started only when no match has been played: everyone then starts on zero, with the teams taken from the fixture list. Otherwise the run fails instead of simulating from zeros.
- **`backtest` is deprecated and refused**, regardless of season state. Use the explicit-UTC frozen-origin evaluation below.
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

### Frozen-origin outcome evaluation

The old `backtest` command is deprecated and always refuses to run. Round numbers
are not chronological boundaries (postponements leak future scores), and unfinished
standings are not final outcomes. Use the offline chronological evaluator instead:

```bash
python -m league_outcome_simulator.evaluation \
  --events-file /path/to/historical-events.json \
  --origin 2025-01-01T00:00:00Z \
  --output-dir /path/to/new-evaluation-directory
```

The input is `{"events": [...]}` with unique positive integer SofaScore event IDs
(booleans and string IDs are rejected), `startTimestamp`
(Unix seconds), `status.type`, `homeTeam.name`, `awayTeam.name`, and nonnegative
integer `homeScore.current` / `awayScore.current`. Supply one competition/season;
the evaluator does not fetch or validate season completeness. It never accesses
the cache or network and never auto-builds Rust. Rebuild the editable extension
before use. Existing `evaluation.json` files are never overwritten.

Optional `resultAvailableTimestamp` supplies explicit result availability in Unix
seconds; when absent or null, kickoff + 3 hours is used and audited as inferred. Explicit availability must be finite
and no earlier than kickoff. Availability and its source are audited for each training row.
Only resolved matches with **result availability strictly before origin** train the
model. This conservative completion approximation excludes boundary-crossing
matches, but cannot establish actual publication times, long interruptions, or
later corrections from retrospective records. Resolved fixtures starting at or
after origin form the test block; unresolved, missing-score, boundary and unknown
training-team exclusions are recorded. Team statistics (total/home/away) and the
Laplace-alpha-1 empirical H/D/A baseline use training scores only. Neither is
updated during testing. Empty training/test sets fail explicitly.

Deterministic H/D/A probabilities sum the actual Rust sampling score grid; no
statistical formulas are duplicated in Python. Model constants and venue
normalization are unchanged. All three predictors (model, uniform, empirical)
score the identical fixtures. Brier is the mean **sum of three** squared errors;
log loss is the mean negative natural logarithm with an explicit observed-class
floor of `1e-15` (floor applications are reported per fixture). Predictions must
be finite, nonnegative and sum to one within `1e-10`; invalid rows abort rather
than being silently omitted or clipped.

`evaluation.json` records origin, input SHA-256, assumptions, constants, training
tables and IDs, test IDs, exclusion reasons, per-fixture probabilities/scores and
aggregate metrics. One held-out block is preliminary evidence only, not proof of
generalization. Keep a later untouched block before tuning or normalization changes.

## Outputs

Each run creates a directory under `results/<league>/<date>/<time>/` with:

- `simulation.txt`: human-readable summary
- `manifest.json`: reproducible run metadata
- `results.json`: machine-readable summary when JSON output is enabled
- `probabilities.png`: chart when plotting is enabled
- Chronological evaluation writes a separate `evaluation.json` in its explicit output directory.

The manifest includes:

- tournament and season ids, season name
- data source (`network` / `cache` / `mixed` / `snapshot`), fetched-at time and age
- seed
- stop reason
- elapsed time
- mean Monte Carlo sampling SE estimate (not predictive accuracy)
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

- The CLI reports **mean Monte Carlo sampling standard error (SE)** in percentage points: the average of `100 * sqrt(p * (1-p) / N)` over team-position cells. `--target-pp-error` controls this sampling stopping threshold, conditional on fixed inputs and model and effectively independent simulations. It is **not predictive accuracy**, team-strength/model uncertainty, or a simultaneous confidence bound. Averaging hides larger individual cell errors; unobserved outcomes have plug-in SE zero. More simulations do not repair model bias. Existing JSON keys `target_pp_error` and `final_pp_error` are retained for compatibility.
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
