# Contributing

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -e .[dev]
python -m maturin develop --release
```

On Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e .[dev]
python -m maturin develop --release
```

## Validation

Run both Python and Rust tests before opening a PR:

```bash
pytest
cargo test --release
```

## Guidelines

- Keep changes small and focused.
- Prefer extending the Rust backend for heavy simulation logic.
- Keep Python focused on orchestration, IO and reporting.
- Update `README.md` when CLI usage, outputs or install steps change.
- If you touch dependencies, update `pyproject.toml`, `requirements.txt` or `Cargo.toml` as needed.

## CLI Principles

- `python -m league_outcome_simulator` should remain easy for end users.
- Non-interactive workflows should keep working in CI and headless servers.
- `--no-gui` and `--plot off` should avoid opening windows.

## Reporting Bugs

Please include:

- operating system
- Python version
- Rust version
- full command used
- traceback or error output
- whether the issue happens with a saved snapshot too

## Pull Requests

- Describe the user-visible change clearly.
- Mention any performance or model behavior implications.
- Note if the change affects reproducibility, outputs or CLI flags.
