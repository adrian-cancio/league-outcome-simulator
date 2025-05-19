# rust_sim

Rust-accelerated simulation for football-probs.

This package provides a high-performance Rust implementation of the Dixon-Coles simulation model, exposed as a Python extension via PyO3 and maturin.

## Prerequisites

Before building and running the Rust simulation extension, ensure you have the following installed:

**Common requirements**:
- Python 3.8 or higher
- Rust toolchain (cargo, rustc) version 1.60 or higher
- maturin (for building the Python extension)

**Windows**:
- Visual Studio 2019 or later with "Desktop development with C++" workload
- Python headers (usually included with the installer)

**Linux**:
- build-essential (gcc, make)
- python3-dev (development headers for Python)

**macOS**:
- Xcode Command Line Tools (`xcode-select --install`)

## Installation and Build

1. Install Python and Rust as per prerequisites.
2. Install `maturin` if not already installed:

   ```bash
   pip install maturin
   ```

3. From the project root directory, build and install the extension in development mode:

   ```bash
   maturin develop --release
   ```

   This will compile the Rust code and install the resulting Python package locally.

4. Alternatively, to build and publish a wheel:

   ```bash
   maturin build --release
   ```

   The generated wheel files will be located in `target/wheels/`.

## Usage

Import and use the simulation function in your Python code:

```python
from simulation import simulate_season

# Example usage:
base_table = [...]       # existing standings data
fixtures = [...]         # list of remaining fixtures
results = simulate_season(base_table, fixtures)
print(results)
```

Replace `base_table` and `fixtures` with your actual data structures as described in the documentation.

## Project Organization

A suggested folder structure for better organization:

```
football-probs/
├── football_probs/         # Python package
│   ├── __init__.py
│   ├── __main__.py        # Entry point for `python -m football_probs`
│   ├── cli.py             # CLI interface module
│   ├── data.py            # Data fetching client
│   ├── models.py          # Simulation models
│   ├── simulation.py      # Rust wrapper
│   ├── utils.py           # Helper functions
│   └── visualization.py   # Plotting and text output
├── src/                    # Rust library source (PyO3 extension)
├── target/                 # Rust build artifacts
├── tests/                  # (Optional) unit tests
├── examples/               # (Optional) example scripts
├── README.md
├── pyproject.toml
└── Cargo.toml
```

## Running the CLI

Once the extension and package are installed (see Installation and Build), you can run the simulation from the command line:

```bash
python -m football_probs
```

Alternatively, after packaging you may install a script entry point via your package manager or add one in `pyproject.toml` under `[project.scripts]`:

```toml
[project.scripts]
football-probs = "football_probs.cli:main"
```