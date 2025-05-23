"""
Football probability simulation main module.
This program calculates the probabilities of different final league positions 
for teams in various football leagues based on current standings and remaining fixtures.
"""
import time
import os
from datetime import datetime
from collections import Counter
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from tqdm import tqdm
import sys
import importlib.util
from pathlib import Path
import msvcrt  # Added for non-blocking key detection on Windows

# dependency check at startup
required_packages = [
    "selenium", "tqdm", "pandas", "matplotlib", "numpy", "scipy"
]
missing = [pkg for pkg in required_packages if importlib.util.find_spec(pkg) is None]
if missing:
    print(f"❌ Missing dependencies: {', '.join(missing)}")
    print("Please install them with: pip install -r requirements.txt")
    sys.exit(1)

# Use package-relative imports
from .data import SofaScoreClient
from .simulation import simulate_season
from .visualization import visualize_results, print_simulation_results
from .utils import process_team_colors, format_duration

# Configuration constants
MAX_SIMULATIONS = 1_000_000  # Maximum number of simulations
MAX_SIMULATION_TIME_SECONDS = 600  # Maximum simulation time in seconds (10 minutes)
HOME_ADVANTAGE = 1.25  # Home advantage factor (1.0 = neutral, higher = more advantage)
NUM_WORKERS = max(1, os.cpu_count() - 1)  # Use all CPU cores but one

# Available leagues with their tournament IDs
LEAGUES = {
    1: (17, "Premier League"),
    2: (8, "La Liga"),
    3: (23, "Serie A"),
    4: (35, "Bundesliga"),
    5: (34, "Ligue 1"),
    6: (37, "Eredivisie"),
    7: (242, "MLS"),
    8: (325, "Brasileirão Serie A"),
    9: (155, "Liga Profesional de Fútbol"),
    10: (54, "La Liga 2"),
    11: (18, "Championship"),
    12: (24, "League One"),
    13: (44, "2. Bundesliga"),
    14: (53, "Serie B"),
    15: (390, "Brasileirão Série B"),
    16: (703, "Primera Nacional"),
    17: (203, "Russian Premier League"),
    18: (1127, "Liga F"),
    19: (23608, "Serie B Femminile"),
    20: (2288, "2. Frauen-Bundesliga"),
    21: (13363, "USL Championship"),
    22: (18641, "MLS Next Pro"),
    99: (None, "Enter custom ID"),  # Option for custom tournament ID
}

# Global Selenium driver management
GLOBAL_DRIVER = None

def initialize_global_driver():
    """Initialize the global Selenium driver if it doesn't exist."""
    global GLOBAL_DRIVER
    if GLOBAL_DRIVER is None:
        print("🔄 Initializing global Selenium driver...", end="", flush=True)
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("user-agent=Mozilla/5.0")
        service = Service(log_path="nul")
        try:
            GLOBAL_DRIVER = webdriver.Chrome(service=service, options=chrome_options)
            return True
        except Exception as e:
            print(f"❌ Error initializing global driver: {e}")
            GLOBAL_DRIVER = None
            return False
    return True

def cleanup_global_driver():
    """Close the global driver when the program ends."""
    global GLOBAL_DRIVER
    if GLOBAL_DRIVER is not None:
        try:
            GLOBAL_DRIVER.quit()
        except:
            pass
        GLOBAL_DRIVER = None

def main():
    """Main function to run the football probability simulation."""
    # Display available leagues
    print("⚽ Available leagues:")
    for idx, (_, name) in LEAGUES.items():
        print(f"{idx}. {name}")
        
    # Get user league selection
    try:
        choice = int(input("Select league number: "))
        if choice == 99:
            tournament_id = int(input("Enter tournament ID: "))
            league_name = "Custom league"
        else:
            tournament_id, league_name = LEAGUES.get(choice)
        if tournament_id is None and choice != 99:
            raise ValueError("Invalid tournament ID")
    except (ValueError, TypeError):
        print("❌ Invalid selection.")
        return

    # Initialize Selenium driver and client
    initialize_global_driver()
    print(f"🔄 Initializing SofaScore for {league_name} (ID: {tournament_id})...")
    client = SofaScoreClient(GLOBAL_DRIVER)

    # Get current season ID
    print("🔄 Getting current season ID...")
    season_id = client.get_current_season_id(tournament_id)
    if not season_id:
        print("❌ Could not get season ID.")
        cleanup_global_driver()
        return

    # Get league table (overall standings)
    print("🔄 Getting overall league table...")
    base_table = client.get_league_table(tournament_id, season_id)
    if not base_table:
        print("❌ Could not get league table.")
        cleanup_global_driver()
        return

    # Get remaining fixtures for simulation
    print("🔄 Getting remaining fixtures...")
    fixtures = client.get_remaining_fixtures(tournament_id, season_id)
    if not fixtures:
        print("❌ Could not get remaining fixtures.")
        cleanup_global_driver()
        return
        
    # Get home and away standings for more accurate simulation
    print("🔄 Getting home league table...")
    home_table = client.get_home_league_table(tournament_id, season_id)
    print("🔄 Getting away league table...")
    away_table = client.get_away_league_table(tournament_id, season_id)
    
    # Get team colors for visualization
    print("🔄 Fetching team colors for visualization...")
    team_colors = client.get_team_colors(tournament_id, season_id)
    
    # Close the Selenium driver now that all API data is fetched
    print("🔄 Closing SofaScore driver...")
    cleanup_global_driver()

    # Initialize counters for each team's final positions
    position_counts = {team: Counter() for team in [row[0] for row in base_table[1:]]}
    # Track full final table occurrences
    table_counter = Counter()
    start_time = time.time()

    # Check if there are fixtures to simulate
    if not fixtures:
        print("❌ No fixtures to simulate.")
        return

    # Run simulations
    print(f"🔄 Running up to {MAX_SIMULATIONS} simulations (max {MAX_SIMULATION_TIME_SECONDS}s)...")
    print("Press 'q' to stop the simulation early.")
    # Display maximum simulation time using reusable formatter
    max_time_str = format_duration(MAX_SIMULATION_TIME_SECONDS)
    print(f"⏳ Note: Maximum simulation time is {max_time_str}.")
    stop = False
    sim_count = 0

    for i in tqdm(range(MAX_SIMULATIONS), desc="Simulating seasons", unit="simulation"):
        # Check for user key press to stop simulation early
        if msvcrt.kbhit():
            key = msvcrt.getch()
            if key.lower() == b'q':
                sim_count = i
                print(f"⏸ Simulation stopped at simulation number {i}")
                stop = True
                break

        # Check if we've exceeded the maximum simulation time
        if time.time() - start_time > MAX_SIMULATION_TIME_SECONDS:
            print("⏳ Maximum simulation time reached. Stopping early.")
            break

        sim_count = i + 1
        # Simulate the remainder of the season using Rust implementation
        simulated_results = simulate_season(base_table, fixtures, home_table, away_table)
        # Record the table ordering
        table_counter[tuple(team for team, _ in simulated_results)] += 1
        # Record the positions from this simulation
        for pos, (team, _) in enumerate(simulated_results, 1):
            position_counts[team][pos] += 1

    # Calculate the actual number of simulations completed
    num_simulations = sum(sum(counter.values()) for counter in position_counts.values()) // len(position_counts)
    print(f"✅ Completed {num_simulations} simulations")

    # Record end time and calculate elapsed time for the simulation
    end_time = time.time()
    elapsed_time = end_time - start_time

    # Process team colors for visualization
    processed_colors = process_team_colors(team_colors)

    # Prepare a single results directory for this run organized by league, date and time
    sanitized_league = league_name.replace(' ', '_')
    date_str = datetime.now().strftime('%Y-%m-%d')  # YYYY-MM-DD
    time_str = datetime.now().strftime('%H-%M-%S')  # HH-MM-SS
    run_dir = Path('results') / sanitized_league / date_str / time_str
    # Print text results
    # Pass number of remaining fixtures for match count calculations
    num_fixtures = len(fixtures)
    print_simulation_results(position_counts, num_simulations, base_table, table_counter, run_dir, elapsed_time, num_fixtures)
    # Show visualization and save image
    visualize_results(position_counts, num_simulations, processed_colors, base_table, run_dir)


if __name__ == "__main__":
    main()
