# League Outcome Simulator

A high-performance football season simulation tool that calculates the probabilities of different final league positions for teams in various leagues.

This package provides:
1. A Rust-accelerated implementation of the Dixon-Coles simulation model
2. Python wrapper for easy integration
3. Data collection from SofaScore
4. Visualization of simulation results

## Prerequisites

Before building and running the simulation, ensure you have the following installed:

**Common requirements**:
- Python 3.8 or higher
- Rust toolchain (cargo, rustc) version 1.60 or higher
- (The Rust extension will compile automatically on first import; no manual build tool needed)

**Windows**:
- Visual Studio 2019 or later with "Desktop development with C++" workload
- Python headers (usually included with the installer)

**Linux**:
- build-essential (gcc, make)
- python3-dev (development headers for Python)

**macOS**:
- Xcode Command Line Tools (`xcode-select --install`)

## Installation

1. Install Python and Rust as per prerequisites.

2. Clone the repository:
   ```bash
   git clone https://github.com/adrian-cancio/league-outcome-simulator.git
   cd league-outcome-simulator
   ```

3. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Ensure Rust toolchain is installed (cargo, rustc). The Python wrapper will compile the Rust extension on first import—no further manual build steps are required.

## Usage

### Command-line Interface

Run the simulation from the command line:

```bash
python -m league_outcome_simulator
```

Follow the prompts to select a league and view simulation results.

### API Usage

Import and use in your Python code:

```python
from league_outcome_simulator.simulation import simulate_season
from league_outcome_simulator.data import SofaScoreClient

# Initialize data client
client = SofaScoreClient()

# Get data for Premier League (ID: 17)
tournament_id = 17
season_id = client.get_current_season_id(tournament_id)
base_table = client.get_league_table(tournament_id, season_id)
fixtures = client.get_remaining_fixtures(tournament_id, season_id)
home_table = client.get_home_league_table(tournament_id, season_id)
away_table = client.get_away_league_table(tournament_id, season_id)

# Run simulation
results = simulate_season(base_table, fixtures, home_table, away_table)
print(results)
```

## Project Structure

```
league-outcome-simulator/
├── league_outcome_simulator/         # Python package
│   ├── __init__.py
│   ├── __main__.py         # Entry point for `python -m league_outcome_simulator`
│   ├── cli.py              # CLI interface module
│   ├── data.py             # Data fetching client
│   ├── models.py           # Simulation models
│   ├── simulation.py       # Rust wrapper
│   ├── utils.py            # Helper functions
│   └── visualization.py    # Plotting and text output
├── src/                    # Rust library source (PyO3 extension)
│   └── lib.rs              # Rust implementation
├── requirements.txt        # Python dependencies
├── README.md
├── pyproject.toml          # Python build configuration
└── Cargo.toml              # Rust build configuration
```

## Dependencies

The project requires the following Python packages (included in requirements.txt):
- selenium - For web scraping data
- tqdm - For progress bars
- pandas - For data manipulation
- matplotlib - For visualization
- numpy - For numerical operations
- scipy - For statistical functions

## License

[MIT License](LICENSE)