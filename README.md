# League Outcome Simulator

A high-performance football season simulation tool that calculates the probabilities of different final league positions for teams in various leagues.

## Features

*   **Accurate Predictions**: Utilizes the Dixon-Coles statistical model, accelerated with a Rust backend, for realistic match outcome simulations.
*   **Comprehensive Data**: Fetches up-to-date league standings (overall, home, away) and remaining fixtures from SofaScore.
*   **Flexible Usage**:
    *   Easy-to-use Command-Line Interface (CLI) for quick simulations.
    *   Python API for integration into custom workflows and applications.
*   **Performance**: Leverages Rust for computationally intensive simulation tasks, enabling a large number of simulations for robust probability estimates.
*   **Automatic Build**: The Rust extension compiles automatically on the first import, requiring no manual Rust build steps from the user.
*   **Result Visualization**: Provides clear textual output of simulation results, including probabilities for each team's final league position.

## Prerequisites

Before building and running the simulation, ensure you have the following installed:

**Common Requirements**:
*   Python 3.8 or higher.
*   Rust toolchain (including `cargo` and `rustc`) version 1.60 or higher. You can install it from [rustup.rs](https://rustup.rs/).
    *   *Note*: The Rust extension is compiled automatically by the Python wrapper when first imported. You do not need to manually run `cargo build` unless you are developing the Rust library itself.

**Operating System Specific Requirements**:

*   **Windows**:
    *   Visual Studio 2019 or later with the "Desktop development with C++" workload installed. This provides the necessary C++ build tools and linkers for Rust.
    *   Python development headers (usually included with the Python installer if "Add Python to PATH" and "Install development libraries" options are selected).
*   **Linux (Debian/Ubuntu based)**:
    *   `build-essential` (for C/C++ compilers like gcc, make):
        ```bash
        sudo apt update
        sudo apt install build-essential
        ```
    *   `python3-dev` (development headers for Python):
        ```bash
        sudo apt install python3-dev
        ```
*   **macOS**:
    *   Xcode Command Line Tools:
        ```bash
        xcode-select --install
        ```

## Installation

1.  **Install Prerequisites**: Ensure Python, the Rust toolchain, and the necessary OS-specific build tools (see above) are installed.

2.  **Clone the Repository**:
    ```bash
    git clone https://github.com/adrian-cancio/league-outcome-simulator.git
    cd league-outcome-simulator
    ```

3.  **Install Python Dependencies**:
    It's recommended to use a virtual environment:
    ```bash
    python -m venv venv
    # Activate the virtual environment
    # On Windows (pwsh or cmd):
    # .\venv\Scripts\Activate.ps1
    # or
    # .\venv\Scripts\activate.bat
    # On macOS/Linux (bash/zsh):
    # source venv/bin/activate
    
    pip install -r requirements.txt
    ```

4.  **Rust Extension Compilation**:
    The Python wrapper will automatically compile the Rust extension (`league_outcome_simulator_rust`) the first time you import or run the simulator. No manual `cargo` commands are needed for standard usage.

## Usage

### Command-Line Interface (CLI)

Run the simulation directly from your terminal:

```bash
python -m league_outcome_simulator
```

The application will then:
1.  Prompt you to select a league from a predefined list or enter a custom SofaScore tournament ID.
2.  Fetch the latest league data (standings, fixtures).
3.  Run a large number of season simulations. Progress will be displayed.
    *   You can typically stop the simulation early by pressing 'q' and then Enter.
4.  Display the calculated probabilities for each team's final league position.

### API Usage

You can integrate the simulation logic into your own Python scripts:

```python
from league_outcome_simulator.simulation import simulate_season
from league_outcome_simulator.data import SofaScoreClient
from league_outcome_simulator.cli import initialize_global_driver, cleanup_global_driver

# It's recommended to initialize and clean up the global Selenium driver 
# if you are making multiple calls or managing the lifecycle externally.
# For a single run, the SofaScoreClient can manage its own driver instance.

if initialize_global_driver():
    try:
        # Initialize data client (can also be initialized without a global driver)
        client = SofaScoreClient() # Uses global driver if initialized, else creates its own

        # Example: Get data for Premier League (ID: 17)
        tournament_id = 17 
        print(f"Fetching data for tournament ID: {tournament_id}")
        season_id = client.get_current_season_id(tournament_id)
        
        if season_id:
            base_table = client.get_league_table(tournament_id, season_id)
            fixtures = client.get_remaining_fixtures(tournament_id, season_id)
            home_table = client.get_home_league_table(tournament_id, season_id)
            away_table = client.get_away_league_table(tournament_id, season_id)

            if base_table and fixtures:
                # The simulate_season function from the Rust core simulates ONE full season.
                # For probability distribution, you'd typically call this many times
                # and aggregate results, similar to how the CLI does.
                print("\\nSimulating one instance of the remaining season...")
                results_one_simulation = simulate_season(base_table, fixtures, home_table, away_table)
                
                print("\\nFinal Standings (from one simulation):")
                for i, (team, stats) in enumerate(results_one_simulation):
                    print(f"{i+1}. {team}: Points - {stats['PTS']}, GF - {stats['GF']}, GA - {stats['GA']}")
            else:
                print("Could not fetch necessary data for simulation.")
        else:
            print(f"Could not find current season ID for tournament {tournament_id}")

    finally:
        cleanup_global_driver()
else:
    print("Failed to initialize the Selenium driver.")

```

## How it Works

The simulation process involves several key stages:

1.  **Data Acquisition (`data.py`)**:
    *   The `SofaScoreClient` class uses `selenium` with a headless web browser to interact with the SofaScore API.
    *   It fetches:
        *   The current season ID for a chosen tournament.
        *   Overall league standings (points, goals for/against, matches played).
        *   Home-specific and Away-specific league standings.
        *   A list of all remaining fixtures for the season.
        *   Team colors (used for visualization if graphical output is implemented).
    *   API responses are cached in memory to reduce redundant calls during a session.

2.  **Core Simulation Logic (Rust - `src/lib.rs`)**:
    *   The heart of the match simulation is implemented in Rust for performance. This Rust code is compiled into a Python extension module (`league_outcome_simulator_rust.pyd` or `.so`).
    *   **Dixon-Coles Model**: Match outcomes (goals scored by home and away teams) are predicted using the Dixon-Coles statistical model. This model is an enhancement of the Poisson distribution, adding a correlation parameter (`rho`) and correction factors (`tau`) to better represent the probabilities of low-scoring results (e.g., 0-0, 1-0, 0-1, 1-1) common in football.
    *   **Expected Goals (Lambdas)**: For each simulated match, the model calculates expected goals (`lambda`) for the home and away teams. These are derived from:
        *   The team's overall average goals scored per game.
        *   The team's average goals scored per game when playing at home or away.
        *   A configurable `HOME_ADVANTAGE` factor is applied to the home team's expected goals.
    *   The Rust function `simulate_season` takes the current league tables and remaining fixtures, then simulates the results of all remaining matches for *one* instance of the season, returning the final calculated league table.
    *   The Rust library uses `rayon` for potential parallelism in its `simulate_bulk` function (though the current CLI uses the single-season simulation in a Python loop).

3.  **Monte Carlo Simulation (Python - `cli.py`)**:
    *   To determine the probabilities of final league positions, the `cli.py` script performs a Monte Carlo simulation.
    *   It utilizes `concurrent.futures.ThreadPoolExecutor` to parallelize calls to the Rust `simulate_season` function (via the Python wrapper in `simulation.py`). This allows multiple season simulations to run concurrently, leveraging multi-core processors for improved performance.
    *   The number of concurrent simulations is typically configured based on the number of available CPU cores (e.g., `os.cpu_count() - 1`).
    *   For each simulation (executed in a separate thread):
        1.  A complete run of the remaining season's fixtures is simulated by the Rust backend.
        2.  The final league table is generated by sorting teams based on standard football tie-breaking rules: Points -> Goal Difference -> Goals Scored.
    *   The main thread in `cli.py` collects the results from the completed simulations and aggregates the final league position achieved by each team across all simulated seasons.
    *   The simulation loop includes logic for early stopping based on a maximum number of simulations, a time limit, or achieving a target error rate, and also allows user interruption.

4.  **Result Aggregation and Output (`cli.py`, `visualization.py`)**:
    *   After all simulations are complete (or an early stopping condition like time limit or target error rate is met), the aggregated position counts are used to calculate the probability of each team finishing in each possible league rank.
    *   The `print_simulation_results` function from `visualization.py` formats and displays these probabilities in a textual table.

## Project Structure

```
league-outcome-simulator/
├── league_outcome_simulator/         # Python package
│   ├── __init__.py
│   ├── __main__.py         # Entry point for `python -m league_outcome_simulator`
│   ├── cli.py              # CLI interface module
│   ├── data.py             # Data fetching client (SofaScore)
│   ├── error_estimation.py # Module for calculating simulation error
│   ├── models.py           # Contains a Python-based DixonColesModel (currently Rust version is primary)
│   ├── simulation.py       # Python wrapper for the Rust simulation core, handles auto-compilation
│   ├── utils.py            # Helper functions
│   └── visualization.py    # Plotting and text output for results
├── src/                    # Rust library source (PyO3 extension)
│   └── lib.rs              # Rust implementation of Dixon-Coles and season simulation
├── results/                # (Optional) Directory for saving simulation outputs
├── target/                 # Rust build artifacts (created automatically)
├── requirements.txt        # Python dependencies
├── README.md
├── pyproject.toml          # Python build configuration (specifies maturin for Rust extension)
└── Cargo.toml              # Rust build configuration and dependencies
```

## Dependencies

*   **Python**: Listed in `requirements.txt`. Key dependencies include `selenium` (for web scraping), `pandas` (for data handling, though less prominent in CLI), `numpy`, `scipy`.
*   **Rust**: Listed in `Cargo.toml`. Key dependencies include `pyo3` (for Python bindings), `rand` (for random number generation), `rayon` (for parallelism).

## Future Ideas & Potential Enhancements

*   **Python-level Parallelism**: The CLI now uses `concurrent.futures.ThreadPoolExecutor` to parallelize calls to the `simulate_season` Rust function. This provides a level of parallelism at the Python script level.
*   **Direct Rust Bulk Simulation**: Modify `cli.py` to utilize the `simulate_bulk` function available in the Rust library. This would move the entire Monte Carlo loop into Rust, potentially offering further performance gains by minimizing Python-Rust inter-process communication overhead compared to the current Python-driven threading.
*   **Advanced Statistical Models**:
    *   Implement Bayesian hierarchical models for team strength estimation.
    *   Explore machine learning approaches (e.g., using historical match data and team/player features).
*   **Parameter Optimization**: Add functionality to optimize model parameters (e.g., `rho` in Dixon-Coles, home advantage factor) using historical league data.
*   **Expanded Data Integration**:
    *   Incorporate player-level statistics (injuries, suspensions, form).
    *   Use betting odds to inform simulations or validate model outputs.
*   **Web Interface**: Develop a user-friendly web application (e.g., using Flask or Django) for easier interaction, configuration, and richer visualization of results.
*   **Graphical Visualizations**: Enhance `visualization.py` to generate plots (e.g., bar charts of position probabilities, evolution of probabilities over time if simulating week-by-week).
*   **Customizable Tie-Breaking Rules**: Allow users to define or select different tie-breaking rules if they vary by league.
*   **In-Play Probabilities**: Extend the model to calculate and update probabilities live as matches are being played.
*   **Historical Season Analysis**: Add functionality to load historical data and test model accuracy against past seasons.

## License

[MIT License](LICENSE)