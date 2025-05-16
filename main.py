import time
import json
import math
from datetime import datetime
from collections import defaultdict, Counter
import numpy as np
from scipy import optimize, special
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from tqdm import tqdm
import pandas as pd
import matplotlib.pyplot as plt
import random
from numba import jit, njit, prange, float64, int64
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import os
from numpy.random import poisson


# Threading and process configuration
NUM_WORKERS = max(1, os.cpu_count() - 1)  # Use all CPU cores but one

# Variable global para el driver de Selenium
GLOBAL_DRIVER = None

def initialize_global_driver():
    """Inicializa el driver global de Selenium si no existe"""
    global GLOBAL_DRIVER
    if GLOBAL_DRIVER is None:
        print("🔄 Inicializando driver global de Selenium...")
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
            print(f"❌ Error inicializando driver global: {e}")
            GLOBAL_DRIVER = None
            return False
    return True

def cleanup_global_driver():
    """Cierra el driver global al finalizar el programa"""
    global GLOBAL_DRIVER
    if GLOBAL_DRIVER is not None:
        try:
            GLOBAL_DRIVER.quit()
        except:
            pass
        GLOBAL_DRIVER = None

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


CURRENT_YEAR = datetime.now().year

MAX_SIMULATIONS = 10_000  # Maximum number of simulations
MAX_SIMULATION_TIME_SECONDS = 120  # Maximum simulation time in seconds
HOME_ADVANTAGE = 1.25  # Home advantage factor (1.0 = neutral, higher = more advantage)

# Color utility functions - MOVED HERE FROM THE BOTTOM OF THE FILE
def get_color_luminance(hex_color):
    """Calculate the perceived brightness of a color (0-255)"""
    hex_color = hex_color.lstrip('#')
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    # Perceived luminance formula (human eye is more sensitive to green)
    return (0.299 * r + 0.587 * g + 0.114 * b)

def get_contrasting_text_color(hex_color):
    """Return black or white depending on which has better contrast with the background"""
    luminance = get_color_luminance(hex_color)
    return '#000000' if luminance > 128 else '#FFFFFF'

def are_colors_similar(color1, color2, threshold=60):
    """Check if two colors are similar based on RGB distance"""
    c1 = color1.lstrip('#')
    c2 = color2.lstrip('#')
    
    r1, g1, b1 = int(c1[0:2], 16), int(c1[2:4], 16), int(c1[4:6], 16)
    r2, g2, b2 = int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16)
    
    # Calculate Euclidean distance in RGB space
    distance = ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5
    return distance < threshold

def random_hex_color():
    """Generate a random hex color"""
    r = random.randint(0, 255)
    g = random.randint(0, 255)
    b = random.randint(0, 255)
    
    # Ensure color isn't too dark (at least one component > 100)
    while max(r, g, b) < 100:
        r = random.randint(0, 255)
        g = random.randint(0, 255)
        b = random.randint(0, 255)
    
    return "#{:02x}{:02x}{:02x}".format(r, g, b)

def darken_color(hex_color, factor=0.7):
    """Darken a color by multiplying RGB components by factor"""
    hex_color = hex_color.lstrip('#')
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    r = max(0, min(255, int(r * factor)))
    g = max(0, min(255, int(g * factor)))
    b = max(0, min(255, int(b * factor)))
    return "#{:02x}{:02x}{:02x}".format(r, g, b)

# Generate deterministic color based on team name
def deterministic_hex_color(team_name):
    """Generate a hex color deterministically based on team name as seed"""
    # Hash the team name for consistent results
    name_hash = sum(ord(c) * (i + 1) for i, c in enumerate(team_name))
    
    # Generate RGB components using the hash
    r = (name_hash * 123) % 256
    g = (name_hash * 457) % 256
    b = (name_hash * 789) % 256
    
    # Ensure color isn't too dark (at least one component > 100)
    if max(r, g, b) < 100:
        brightest = max(r, g, b)
        if brightest > 0:
            factor = 100 / brightest
            r = min(255, int(r * factor))
            g = min(255, int(g * factor))
            b = min(255, int(b * factor))
        else:
            r, g, b = 120, 120, 120  # Default if all were 0
    
    return "#{:02x}{:02x}{:02x}".format(r, g, b)

# Generate complementary color based on primary color and team name
def deterministic_secondary_color(team_name, primary_color):
    """Generate a contrasting secondary color using team name and primary color"""
    # Extract RGB components
    primary = primary_color.lstrip('#')
    r = int(primary[0:2], 16)
    g = int(primary[2:4], 16)
    b = int(primary[4:6], 16)
    
    # Use team name hash to determine color generation method
    name_hash = sum(ord(c) for c in team_name)
    
    if name_hash % 4 == 0:
        # Complementary color (invert)
        r2 = 255 - r
        g2 = 255 - g
        b2 = 255 - b
    elif name_hash % 4 == 1:
        # Rotate hue (shift RGB)
        r2 = b
        g2 = r
        b2 = g
    elif name_hash % 4 == 2:
        # Darken/lighten based on brightness
        brightness = r + g + b
        factor = 0.6 if brightness > 380 else 1.7
        r2 = min(255, max(0, int(r * factor)))
        g2 = min(255, max(0, int(g * factor)))
        b2 = min(255, max(0, int(b * factor)))
    else:
        # Mix with another deterministic color
        mix_hash = (name_hash * 37) % 256
        r2 = (r + mix_hash) % 256
        g2 = (g + mix_hash) % 256
        b2 = (b + mix_hash) % 256
    
    return "#{:02x}{:02x}{:02x}".format(r2, g2, b2)

# Check if colors have good contrast
def is_good_contrast(color1, color2, threshold=120):
    """Check if two colors have sufficient contrast"""
    c1 = color1.lstrip('#')
    c2 = color2.lstrip('#')
    
    r1, g1, b1 = int(c1[0:2], 16), int(c1[2:4], 16), int(c1[4:6], 16)
    r2, g2, b2 = int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16)
    
    # Calculate contrast using perceived brightness difference and color difference
    brightness1 = (r1 * 299 + g1 * 587 + b1 * 114) / 1000
    brightness2 = (r2 * 299 + g2 * 587 + b2 * 114) / 1000
    brightness_diff = abs(brightness1 - brightness2)
    
    color_diff = abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)
    
    return brightness_diff > 70 or color_diff > threshold

def fetch_json_with_selenium(url):
    """Fetch JSON data using the global Selenium driver"""
    global GLOBAL_DRIVER
    print(f"🔄 Getting data from: {url}")
    
    # Intentar inicializar el driver global si no existe
    if GLOBAL_DRIVER is None:
        if not initialize_global_driver():
            print("❌ No se pudo inicializar el driver global")
            return None
    
    try:
        GLOBAL_DRIVER.get(url)
        time.sleep(3)
        body = GLOBAL_DRIVER.find_element("tag name", "body").text
    except Exception as e:
        print(f"❌ Error using global driver: {e}")
        # Intentar reinicializar el driver global
        cleanup_global_driver()
        if not initialize_global_driver():
            print("❌ No se pudo reinicializar el driver global")
            return None
        
        # Segundo intento con nuevo driver
        try:
            GLOBAL_DRIVER.get(url)
            time.sleep(3)
            body = GLOBAL_DRIVER.find_element("tag name", "body").text
        except Exception as e2:
            print(f"❌ Error en segundo intento: {e2}")
            return None

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        print("❌ Error decodificando JSON.")
        return None

class SofaScoreClient:
    BASE_URL = "https://api.sofascore.com/api/v1"
    
    def __init__(self):
        """Initialize the client with a reference to the global Selenium driver"""
        self.driver = None
        # No hay que crear un nuevo driver, solo asegurarse que existe el global
        self.setup_driver()
        # Diccionarios para almacenar los lambdas calculados
        self.team_lambdas = {
            'home': {},    # λ_home por equipo
            'away': {},    # λ_away por equipo
            'global': {},  # λ_global por equipo
        }
    
    def setup_driver(self):
        """Set up a reference to the global Selenium driver"""
        global GLOBAL_DRIVER
        print("🔄 Usando driver global de Selenium para todas las llamadas API...")
        
        # Inicializar el driver global si no existe
        if GLOBAL_DRIVER is None:
            initialize_global_driver()
            
        # Referenciar al driver global
        self.driver = GLOBAL_DRIVER
    
    def fetch_json(self, url):
        """Fetch JSON data from an URL using the global driver"""
        print(f"🔄 Getting data from: {url}")
        
        if self.driver is None:
            print("⚠️ Driver not initialized, attempting to use global driver...")
            self.setup_driver()
            if self.driver is None:
                print("❌ Failed to initialize driver, falling back to fetch_json_with_selenium")
                return fetch_json_with_selenium(url)
        
        try:
            self.driver.get(url)
            time.sleep(2)  # Reduced wait time since driver is warm
            body = self.driver.find_element("tag name", "body").text
            
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                print("❌ Error decoding JSON.")
                return None
        except Exception as e:
            print(f"❌ Error using Selenium driver: {e}")
            print("⚠️ Reinitializando driver global...")
            
            # Reinicializar el driver global
            cleanup_global_driver()
            initialize_global_driver()
            self.driver = GLOBAL_DRIVER
            
            # Seguir usando fetch_json_with_selenium si la reinicialización falla
            if self.driver is None:
                return fetch_json_with_selenium(url)
            
            # Segundo intento con el driver global reinicializado
            try:
                self.driver.get(url)
                time.sleep(3)
                body = self.driver.find_element("tag name", "body").text
                return json.loads(body)
            except Exception as e2:
                print(f"❌ Error en segundo intento: {e2}")
                return fetch_json_with_selenium(url)
    
    def __del__(self):
        """Nothing to clean up since we're using the global driver"""
        pass
    
    def get_current_season_id(self, tournament_id):
        url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/seasons"
        data = self.fetch_json(url)
        if (data and 'seasons' in data):
            seasons = sorted(data['seasons'], key=lambda x: x['id'], reverse=True)
            if seasons:
                return seasons[0]['id']
        return None

    def get_league_table(self, tournament_id, season_id):
        """
        Obtiene tabla general del torneo y calcula λ_global para cada equipo.
        λ_global = goles anotados totales / partidos jugados
        """
        url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/standings/total"
        data = self.fetch_json(url)
        if data and 'standings' in data and data['standings']:
            rows = [['Team', 'M', 'W', 'D', 'L', 'G', 'GA', 'PTS']]
            
            # Recorrer cada equipo y calcular su lambda global
            for row in data['standings'][0]['rows']:
                team_name = row['team']['name']
                matches = row['matches']
                goals_for = row['scoresFor']
                
                # Calcular λ_global solo si han jugado partidos
                if matches > 0:
                    self.team_lambdas['global'][team_name] = goals_for / matches
                else:
                    # Valor predeterminado para equipos sin partidos
                    self.team_lambdas['global'][team_name] = 1.0
                
                rows.append([
                    team_name,
                    matches,
                    row['wins'],
                    row['draws'],
                    row['losses'],
                    goals_for,
                    row['scoresAgainst'],
                    row['points'],
                ])
            return rows
        return None
        
    def get_home_league_table(self, tournament_id, season_id):
        """
        Obtiene tabla de partidos en casa y calcula λ_home para cada equipo.
        λ_home = goles anotados en casa / partidos jugados en casa
        """
        url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/standings/home"
        data = self.fetch_json(url)
        if data and 'standings' in data and data['standings']:
            rows = [['Team', 'M', 'W', 'D', 'L', 'G', 'GA', 'PTS']]
            
            # Recorrer cada equipo y calcular su lambda home
            for row in data['standings'][0]['rows']:
                team_name = row['team']['name']
                matches = row['matches']
                goals_for = row['scoresFor']
                
                # Calcular λ_home solo si han jugado partidos en casa
                if matches > 0:
                    self.team_lambdas['home'][team_name] = goals_for / matches
                else:
                    # Valor predeterminado para equipos sin partidos en casa
                    self.team_lambdas['home'][team_name] = 1.0
                
                rows.append([
                    team_name,
                    matches,
                    row['wins'],
                    row['draws'],
                    row['losses'],
                    goals_for,
                    row['scoresAgainst'],
                    row['points'],
                ])
            return rows
        return None
        
    def get_away_league_table(self, tournament_id, season_id):
        """
        Obtiene tabla de partidos fuera de casa y calcula λ_away para cada equipo.
        λ_away = goles anotados fuera / partidos jugados fuera
        """
        url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/standings/away"
        data = self.fetch_json(url)
        if data and 'standings' in data and data['standings']:
            rows = [['Team', 'M', 'W', 'D', 'L', 'G', 'GA', 'PTS']]
            
            # Recorrer cada equipo y calcular su lambda away
            for row in data['standings'][0]['rows']:
                team_name = row['team']['name']
                matches = row['matches']
                goals_for = row['scoresFor']
                
                # Calcular λ_away solo si han jugado partidos fuera
                if matches > 0:
                    self.team_lambdas['away'][team_name] = goals_for / matches
                else:
                    # Valor predeterminado para equipos sin partidos fuera
                    self.team_lambdas['away'][team_name] = 1.0
                
                rows.append([
                    team_name,
                    matches,
                    row['wins'],
                    row['draws'],
                    row['losses'],
                    goals_for,
                    row['scoresAgainst'],
                    row['points'],
                ])
            return rows
        return None

    def get_remaining_fixtures(self, tournament_id, season_id):
        """
        Get all remaining fixtures for a tournament with pagination support.
        The API returns up to 30 events per page, so we need to iterate through 
        pages until we get fewer than 30 events or a 404 error.
        """
        all_fixtures = []
        page = 0
        
        print(f"📅 Fetching remaining fixtures (pagination enabled)...")
        
        while True:
            url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/events/next/{page}"
            print(f"    Getting page {page} of fixtures...")
            
            data = self.fetch_json(url)
            
            # Check if we got a valid response with events
            if not data or 'events' not in data or not data['events']:
                print(f"    No more fixtures found (reached page {page})")
                break
                
            # Process events from this page
            page_events = []
            for event in data['events']:
                if event['status']['type'] == 'notstarted':
                    page_events.append({
                        'id': event['id'],
                        'h': {'title': event['homeTeam']['name']},
                        'a': {'title': event['awayTeam']['name']},
                        'datetime': event['startTimestamp'],
                    })
            
            # Add events from this page to our collection
            all_fixtures.extend(page_events)
            
            # Check if we got less than 30 events (which means this is the last page)
            if len(data['events']) < 30:
                print(f"    End of fixtures reached (page {page} had {len(data['events'])} events)")
                break
                
            # Move to next page
            page += 1
        
        print(f"📊 Found {len(all_fixtures)} total remaining fixtures across {page+1} pages")
        return all_fixtures

def get_team_colors_from_standings(tournament_id, season_id):
    """Extract team colors from the standings endpoint and replace default blue with random colors"""
    url = f"https://api.sofascore.com/api/v1/unique-tournament/{tournament_id}/season/{season_id}/standings/total"
    data = fetch_json_with_selenium(url)
    team_colors = {}
    if not data or "standings" not in data or not data["standings"]:
        return team_colors
    default_blue = "#374df5"  # Common default color from API
    
    print("\n🎨 Loading team colors...")
    # Process all teams from the standings
    for standing in data.get("standings", []):
        for row in standing.get("rows", []):
            team = row.get("team", {})
            team_name = team.get("name")
            if not team_name:
                continue
            # Extract colors from the team data
            team_colors_data = team.get("teamColors", {})
            primary_color = team_colors_data.get("primary")
            secondary_color = team_colors_data.get("secondary")
            # Handle primary color - replace default blue with random
            is_primary_replaced = False
            if not primary_color or primary_color == default_blue:
                primary_color = deterministic_hex_color(team_name)
                is_primary_replaced = True
            # Handle secondary color - replace default blue with random or use primary
            is_secondary_replaced = False
            if not secondary_color or secondary_color == default_blue:
                if is_primary_replaced:
                    # If primary was random, make a different random secondary
                    secondary_color = deterministic_hex_color(team_name + "secondary")
                else:
                    # If primary was real, use it for secondary too
                    secondary_color = primary_color
                is_secondary_replaced = True
            
            team_colors[team_name] = {
                "primary": primary_color,
                "secondary": secondary_color,
            }
    return team_colors

def simulate_match(xg_home, xg_away):
    """Simulate a football match using Poisson distribution with realistic probabilities
    
    Args:
        xg_home: Expected goals for home team
        xg_away: Expected goals for away team
    
    Returns:
        Tuple of simulated goals (home_goals, away_goals)
    """
    # Generate goals using Poisson distribution
    home_goals = poisson(xg_home)
    away_goals = poisson(xg_away)
    
    # Small probability of atypical results (goal fest or crazy match)
    if random.random() < 0.03:  # 3% probability
        # Match with many goals or atypical result
        home_goals = poisson(xg_home * 1.5)
        away_goals = poisson(xg_away * 1.5)
    
    return home_goals, away_goals

def dixon_coles_simulate_match(lambda_home, lambda_away, rho):
    """
    Simula un partido usando el modelo Dixon-Coles en lugar de Poisson independientes.
    Muestrea de la distribución bivariada Dixon-Coles.
    
    Args:
        lambda_home: Tasa esperada de goles del equipo local (λ_home)
        lambda_away: Tasa esperada de goles del equipo visitante (λ_away)
        rho: Parámetro de corrección (correlación entre resultados locales y visitantes)
        
    Returns:
        Tupla de goles (goles_local, goles_visitante)
    """
    # Máximo de goles a considerar (ajustar según la liga)
    max_goals = 10
    
    # Crear matriz de probabilidad para todos los posibles resultados
    prob_matrix = np.zeros((max_goals+1, max_goals+1))
    for i in range(max_goals+1):
        for j in range(max_goals+1):
            prob_matrix[i, j] = dixon_coles_probability(i, j, lambda_home, lambda_away, rho)
    
    # Normalizar para asegurar que sume 1
    prob_matrix = prob_matrix / np.sum(prob_matrix)
    
    # Aplanar la matriz y obtener el índice del resultado muestreado
    flat_probs = prob_matrix.flatten()
    flat_index = np.random.choice(len(flat_probs), p=flat_probs)
    
    # Convertir el índice plano de nuevo a coordenadas de la matriz
    home_goals = flat_index // (max_goals+1)
    away_goals = flat_index % (max_goals+1)
    
    return home_goals, away_goals

def simulate_season(base_table, fixtures, home_table=None, away_table=None):
    """Simulate the remainder of the season based on current standings and remaining fixtures,
    using the ultra-optimized Dixon-Coles model with parallel processing.
    
    Args:
        base_table: Overall league standings table
        fixtures: List of remaining matches to simulate
        home_table: Home-only league standings table (optional)
        away_table: Away-only league standings table (optional)
    """
    # Create dictionary with current standings data
    standings = {row[0]: {"PTS": int(row[7]), "GF": int(row[5]), "GA": int(row[6]), "M": int(row[1])} for row in base_table[1:]}
    
    # Initialize optimized Dixon-Coles model with precomputation
    dc_model = DixonColesModel(rho=-0.1, max_goals=8)
    
    # Calculate lambdas from all tables
    dc_model.calculate_lambdas(base_table, home_table, away_table)
    
    # Extract match history for rho estimation
    dc_model.extract_match_history(base_table, home_table, away_table)
    
    # Estimate rho parameter (if enough data)
    estimated_rho = dc_model.estimate_rho()
    dc_model.rho = estimated_rho  # Use the estimated or default value
    
    # Check if any team has reached the maximum number of matches
    max_matches = (len(standings) - 1) * 2  # Each team plays against every other team twice
    completed_teams = {
        team: data.copy() 
        for team, data in standings.items() 
        if data["M"] >= max_matches
    }
    
    # If all teams have completed all matches or no fixtures left, just return current standings
    if len(completed_teams) == len(standings) or not fixtures:
        sorted_teams = sorted(standings.items(), key=lambda x: (-x[1]["PTS"], -(x[1]["GF"] - x[1]["GA"]), -x[1]["GF"]))
        return [team for team, _ in sorted_teams]
    
    # Pre-determine fixed positions for teams that have completed all matches
    if completed_teams:
        # Sort completed teams to establish their relative positions
        sorted_completed = sorted(
            completed_teams.items(), 
            key=lambda x: (-x[1]["PTS"], -(x[1]["GF"] - x[1]["GA"]), -x[1]["GF"])
        )
        # Create a map of completed teams to their relative ranks
        completed_ranks = {team: i for i, (team, _) in enumerate(sorted_completed)}
    else:
        completed_ranks = {}
    
    # Create a copy of current standings for simulation
    simulation_standings = {
        team: data.copy() for team, data in standings.items()
        if team not in completed_teams
    }
    
    # OPTIMIZACIÓN EXTREMA: Simulación de partidos en paralelo
    match_results = dc_model.simulate_matches_parallel(fixtures, home_advantage=HOME_ADVANTAGE)
    
    # Procesar los resultados y actualizar la tabla
    for i, match in enumerate(fixtures):
        h_team = match["h"]["title"]
        a_team = match["a"]["title"]
        
        # Skip matches involving completed teams (this shouldn't happen but as a safeguard)
        if h_team in completed_teams or a_team in completed_teams:
            continue
        
        # Obtener resultados de la simulación en paralelo
        gh, ga = match_results[i]
        
        for team in [h_team, a_team]:
            if team not in simulation_standings:
                simulation_standings[team] = {"PTS": 0, "GF": 0, "GA": 0, "M": 0}
        
        # Update match stats
        simulation_standings[h_team]["GF"] += gh
        simulation_standings[h_team]["GA"] += ga
        simulation_standings[h_team]["M"] += 1
        simulation_standings[a_team]["GF"] += ga
        simulation_standings[a_team]["GA"] += gh
        simulation_standings[a_team]["M"] += 1
        
        # Award points
        if gh > ga:
            simulation_standings[h_team]["PTS"] += 3
        elif ga > gh:
            simulation_standings[a_team]["PTS"] += 3
        else:
            simulation_standings[h_team]["PTS"] += 1
            simulation_standings[a_team]["PTS"] += 1
    
    # Combine completed teams (with fixed ranks) and simulated teams
    final_standings = {}
    final_standings.update(completed_teams)
    final_standings.update(simulation_standings)
    
    # Sort teams based on points, goal difference, goals for
    # But preserve the relative order of teams that have completed all matches
    def sorting_key(item):
        team, data = item
        # Teams with fixed ranks get sorted by their rank first
        if team in completed_ranks:
            # Use a large number (e.g., 10000) to ensure completed teams are always sorted by their rank first
            return (-10000 + completed_ranks[team], -data["PTS"], -(data["GF"] - data["GA"]), -data["GF"])
        # For other teams, sort normally
        return (-data["PTS"], -(data["GF"] - data["GA"]), -data["GF"])
    
    sorted_teams = sorted(final_standings.items(), key=sorting_key)
    return [team for team, _ in sorted_teams]
            
def visualize_results(position_counts, num_simulations, team_colors, base_table):
    """Visualize the simulation results with a stacked bar chart"""
    print("\n📊 Showing results in chart...")
    
    # Calculate total matches in a season
    total_teams = len([row for row in base_table if row[0] != 'Team'])  # Count all rows except header
    total_matches = (total_teams - 1) * 2  # Each team plays against all others twice
    
    # Create dictionaries from base_table
    current_positions = {}
    current_points = {}
    current_matches = {}  # Track matches played
    for i, row in enumerate(base_table[1:], 1):  # Skip header row
        team_name = row[0]
        current_positions[team_name] = i
        current_points[team_name] = int(row[7])  # Points are at index 7
        current_matches[team_name] = int(row[1])  # Matches are at index 1
    
    # Create the data for visualization
    data = []
    for team, pos_counter in position_counts.items():
        for pos, count in pos_counter.items():
            data.append({"Team": team, "Position": pos, "Probability": count / num_simulations * 100})
    df = pd.DataFrame(data)
    
    # Define hatching patterns and subtle patterns
    hatch_patterns = [
        '////', '....', 'xxxx', 'oooo', '||||', '++++', '\\\\\\\\', '----', '****',
        'xx..', '++..', '\\\\..', '//..', '||..', 'oo..',          # Combined patterns
        'x+x+', '\\/\\/\/', '|x|x', 'o-o-', '*/*/',                # Alternating patterns
        '//\\\\', 'xxoo', '++**', '||||||||',                       # Dense patterns
        '++\\\\', 'xx||', 'oo--', '**xx', '..||', 'oo\\\\',         # More combinations
        '///\\\\\\', '...---', 'xxx|||', 'ooo+++'                  # High contrast patterns
    ]
    
    subtle_patterns = [
        '.', '/', 'x', '+', '|', '-', '\\', '*',              # Simple patterns
        '..', '//', 'xx', '++', '||', '--', '\\\\', '**', 'oo',    # Double density
        '.-.', '/-/', 'x-x', '+-+', '|-|', '-.-', '\\-\\',         # Alternate with dashes
        './.', '/./', 'x/x', '+/+', '|/|', '-/-', '\\/\\',         # Alternate with slashes
        '...', '///', 'xxx', '+++', '|||'                         # Triple density
    ]
    
    # Function to get consistent pattern index from team name
    def get_pattern_index(team_name, pattern_list):
        # Use a hash of the team name to get a consistent index
        # This ensures the same team always gets the same pattern
        name_hash = sum(ord(c) for c in team_name)
        return name_hash % len(pattern_list)

    # Create the figure
    fig, ax = plt.subplots(figsize=(14, 8))
    
    # Keep track of which teams have been added to the plot
    all_teams = list(position_counts.keys())
    team_patches = {}  # To store handles for each team for the legend
    
    # Process each position individually
    positions = sorted(df['Position'].unique())
    
    # Global system to avoid label overlaps
    occupied_areas = []  # List of occupied rectangles [(x1,y1,x2,y2),...]
    
    # First pass: Draw all bars and prepare data for labels
    team_labels = []  # List to store pending label information
    for position in positions:
        # Filter data only for this position
        position_data = df[df['Position'] == position]
        
        # Group and calculate probabilities
        prob_by_team = position_data.groupby('Team')['Probability'].sum().reset_index()
        # Add current league position to data for sorting
        prob_by_team['CurrentPosition'] = prob_by_team['Team'].apply(
            lambda team: current_positions.get(team, 999)
        )
        
        # Find team with highest probability for this position (to highlight later)
        if not prob_by_team.empty:
            # Instead of just the max, get the top 3 teams by probability
            top_teams = prob_by_team.nlargest(3, 'Probability')
            top_teams_info = []
        else:
            top_teams_info = []
        
        # Sort by PROBABILITY (ascending) for this specific position
        prob_by_team = prob_by_team.sort_values('Probability', ascending=True)  # Changed to True to make larger bars on top
        
        # Initialize base for this position's segments
        bottom = 0
        x_pos = position  # Center bars exactly at the integer position
        width = 0.8  # Bar width
        segments_to_highlight = []
        # Variable to store the team with highest probability for this position
        top_team = None
        top_prob = 0
        # For each team in order (by probability), draw its segment
        for _, row in prob_by_team.iterrows():
            team = row['Team']
            prob = row['Probability']
            # Save team with highest probability
            if prob > top_prob:
                top_prob = prob
                top_team = team
            # Draw this segment
            if team in team_colors:
                primary = team_colors[team]["primary"]
                secondary = team_colors[team]["secondary"]
                fill_color = primary
                # Determine the pattern based on whether colors are the same or different
                if secondary != primary:
                    pattern_idx = get_pattern_index(team, hatch_patterns)
                    hatch = hatch_patterns[pattern_idx]
                    edge_color = secondary
                else:
                    pattern_idx = get_pattern_index(team, subtle_patterns)
                    hatch = subtle_patterns[pattern_idx]
                    edge_color = darken_color(primary, factor=0.5)
            else:
                # Generate deterministic colors based on team name
                fill_color = deterministic_hex_color(team)
                edge_color = deterministic_secondary_color(team, fill_color)
                
                # Ensure good contrast between primary and secondary colors
                if not is_good_contrast(fill_color, edge_color):
                    # Try alternative generation methods until good contrast is achieved
                    attempts = 0
                    while not is_good_contrast(fill_color, edge_color) and attempts < 5:
                        # Modify the team name slightly for a different hash result
                        modified_team = team + str(attempts)
                        edge_color = deterministic_secondary_color(modified_team, fill_color)
                        attempts += 1
                    
                    # If still no good contrast, use complementary color (invert)
                    if not is_good_contrast(fill_color, edge_color):
                        # Strip the # and convert to RGB
                        fill_hex = fill_color.lstrip('#')
                        r = int(fill_hex[0:2], 16)
                        g = int(fill_hex[2:4], 16)
                        b = int(fill_hex[4:6], 16)
                        
                        # Create complementary color (invert RGB values)
                        r2 = 255 - r
                        g2 = 255 - g
                        b2 = 255 - b
                        edge_color = f"#{r2:02x}{g2:02x}{b2:02x}"
                
                pattern_idx = get_pattern_index(team, hatch_patterns)
                hatch = hatch_patterns[pattern_idx]
                team_colors[team] = {"primary": fill_color, "secondary": edge_color}
                print(f"⚠️ Warning: No color found for team '{team}'. Using deterministic colors based on team name.")
            
            # Draw this segment
            rect = ax.bar(x_pos, prob, width=width, bottom=bottom, color=fill_color, 
                          edgecolor=edge_color, linewidth=1.5, label="")
            rect[0].set_hatch(hatch)
            
            # Save this rectangle for the legend if we don't have it yet
            if team not in team_patches:
                team_patches[team] = rect[0]
            
            # Instead of just saving top teams, save all with their details
            segments_to_highlight.append((rect[0], bottom, prob, team))
            # Update base for next segment
            bottom += prob
        
        # Store top team info for second pass
        if top_team and top_prob >= 5.0:
            # Find segment data for this team
            for segment, bottom_pos, height, team_name in segments_to_highlight:
                if team_name == top_team:
                    # Group all information we'll need for labeling
                    team_labels.append({
                        'team': team_name,
                        'position': position,
                        'top_y': bottom_pos + height,  # Top Y position of the bar
                        'probability': height,
                        'primary_color': team_colors[team_name]['primary'],
                        'secondary_color': team_colors[team_name]['secondary'],
                    })
        
        # Show percentages inside ALL bars with sufficient height
        for segment, bottom_pos, height, team_name in segments_to_highlight:
            # INCREASE minimum needed to show text and check if text fits
            if height < 2.0:  # Reduced from 3% to 2% to show more percentages
                continue
            rect = ax.bar(x_pos, prob, width=width, bottom=bottom, color=fill_color, 
                          edgecolor=edge_color, linewidth=1.5, label="")
            # Calculate central position of segment
            center_x = x_pos
            center_y = bottom_pos + height / 2
            rect[0].set_hatch(hatch)
            # Using new functions for similarity detection
            team_primary = team_colors[team_name]["primary"]
            team_secondary = team_colors[team_name]["secondary"]
            is_very_light_primary = get_color_luminance(team_primary) > 200
            is_very_light_secondary = get_color_luminance(team_secondary) > 200
            
            # Improved color similarity verification
            colors_identical = team_primary == team_secondary
            colors_similar = are_colors_similar(team_primary, team_secondary, 40) if not colors_identical else True
            
            # Define large bar check
            is_large_bar = height >= 5.0

            # Adjust for specific cases with identical or very similar colors
            if colors_identical:
                # If colors are identical, use automatic contrast
                bg_color = team_primary
                text_color = get_contrasting_text_color(bg_color)
                edge_color = 'black' if is_very_light_primary else 'white'
                edge_width = 1.0
            elif colors_similar:
                # If they are similar but not identical, adjust for contrast
                bg_color = team_secondary
                text_color = get_contrasting_text_color(bg_color)
                edge_color = 'black' if is_very_light_secondary else 'white'
                edge_width = 1.0
            else:
                # If they have good contrast between them
                text_color = team_primary
                bg_color = team_secondary
                edge_color = 'none'
                edge_width = 0
            
            # Define percentage text
            percent_text = f"{height:.1f}%"
                
            # Adjust box width to the width of sub-bar
            box_width = width * 0.7  # Slightly smaller than bar width so it fits well
            
            # For large or medium bars, add percentage with more visible text
            if is_large_bar:
                # Larger size for large bars
                font_size = min(8, max(6, height / 2))  # Increased minimum and maximum size
                ax.text(center_x, center_y, percent_text, 
                        ha='center', va='center', fontsize=font_size, fontweight='bold',
                        color=text_color, 
                        bbox=dict(facecolor=bg_color, edgecolor=edge_color,
                                alpha=0.85, pad=0.2, boxstyle='round,pad=0.2,rounding_size=0.2', 
                                linewidth=edge_width))
            else:
                # For smaller bars, adaptable size but not too small
                compact_font_size = min(7, max(5.5, height / 2))  # Higher minimum size (5.5 instead of 4)
                # Reduce vertical padding to fit better in small bars
                ax.text(center_x, center_y, percent_text, 
                        ha='center', va='center', fontsize=compact_font_size, fontweight='bold',
                        color=text_color,
                        bbox=dict(facecolor=bg_color, edgecolor=edge_color,
                                alpha=0.85, pad=0.15, boxstyle='round,pad=0.15,rounding_size=0.1', 
                                linewidth=edge_width))

    # NEW APPROACH: Place all team names as "column headers"
    # Create dictionary to keep only ONE team per position (the one with highest probability)
    top_team_by_position = {}
    
    for label_info in team_labels:
        position = label_info['position']
        
        # Only save the team with highest probability for each position
        if position in top_team_by_position:
            if label_info['probability'] > top_team_by_position[position]['probability']:
                top_team_by_position[position] = label_info
        else:
            top_team_by_position[position] = label_info
    
    # Fixed height for all labels (as a column header)
    header_y = 103  # Increased slightly to give more space to title

    # Improved function to abbreviate names
    def abbreviate_name(name, max_length=8):  # Reduced maximum to 8 characters
        if len(name) <= max_length:
            return name
        # Try abbreviating using initials
        if ' ' in name:
            words = name.split()
            if len(words) == 2:
                # First word + initial of second
                if len(words[0]) > max_length - 2:
                    return words[0][:max_length-2] + "." + words[1][0] + "."
                else:
                    return words[0] + " " + words[1][0] + "."
            else:
                # For more words, use only initials except first
                first = words[0][:min(5, len(words[0]))]  # Limit first word to 5 characters
                rest = ''.join(w[0] + '.' for w in words[1:])
                return first + " " + rest
        
        # If no spaces, truncate
        return name[:max_length-2] + ".."

    # Detect and resolve possible horizontal overlap
    # Group adjacent positions to verify spacing
    position_groups = []
    current_group = []
    
    for pos in sorted(top_team_by_position.keys()):
        if not current_group or pos - current_group[-1] == 1:  # Adjacent positions
            current_group.append(pos)
        else:
            if current_group:  # If group has elements
                position_groups.append(current_group)
            current_group = [pos]
    
    if current_group:  # Don't forget last group
        position_groups.append(current_group)
    
    # Place team names as headers, processing by groups
    for group in position_groups:
        # For groups of adjacent positions, adjust sizes and rotations
        if len(group) > 1:
            # More compact for large groups
            compact_mode = len(group) > 3
            font_sizes = {}
            rotations = {}
            
            # First pass: assign base sizes and detect conflicts
            for pos in group:
                info = top_team_by_position[pos]
                team_name = info['team']
                display_name = abbreviate_name(team_name, 7 if compact_mode else 8)
                # Smaller font size for all to avoid overlaps
                font_sizes[pos] = min(6.0, max(4.5, 8 - len(display_name) * 0.3))
                # Alternate rotation on adjacent positions
                rotations[pos] = 15 if pos % 2 == 0 else -15
            
            # Second pass: Actually place the names
            for pos in group:
                info = top_team_by_position[pos]
                team_name = info['team']
                primary = info['primary_color']
                secondary = info['secondary_color']    
                display_name = abbreviate_name(team_name, 7 if compact_mode else 8)
                
                # Check color contrast (same as for percentages)
                is_very_light = sum(int(primary.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)) > 650
                is_white = is_very_light and get_color_luminance(primary) > 240  # Special check for white
                colors_similar = are_colors_similar(primary, secondary)
                colors_identical = primary == secondary
                
                # Adjust colors according to case - new logic with borders matching text color
                if colors_identical:
                    # If colors are identical, use automatic contrast
                    bg_color = secondary
                    text_color = get_contrasting_text_color(bg_color)
                    edge_color = text_color  # Border same as text
                    edge_width = 1.0
                else:
                    # Use primary color for text, secondary for background
                    bg_color = secondary
                    text_color = primary
                    edge_color = text_color  # Border same as text
                    edge_width = 0.8
                
                # Add name with adjusted rotation and size
                ax.text(pos, header_y, display_name,
                       ha='center', va='bottom', 
                       fontsize=font_sizes[pos],
                       fontweight='bold', rotation=rotations[pos],
                       color=text_color,
                       bbox=dict(facecolor=secondary, edgecolor=edge_color,
                                boxstyle='round,pad=0.15',
                                alpha=0.9, 
                                linewidth=edge_width))
        else:
            # For isolated positions, use standard approach
            pos = group[0]
            info = top_team_by_position[pos]
            team_name = info['team']    # Always use abbreviated name in compact mode
            primary = info['primary_color']
            secondary = info['secondary_color']    
            display_name = abbreviate_name(team_name, 9)  # Slightly longer for isolated positions
            # Check color contrast
            is_very_light = sum(int(primary.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)) > 650
            is_white = is_very_light and get_color_luminance(primary) > 240  # Special check for white
            colors_similar = are_colors_similar(primary, secondary)
            colors_identical = primary == secondary
            
            # Adjust colors according to case - using same logic as for percentages
            if colors_identical:
                # If colors are identical, use automatic contrast
                bg_color = secondary
                text_color = get_contrasting_text_color(bg_color)  # This will give black for white backgrounds
                edge_color = 'black' if is_very_light else 'white'
                edge_width = 1.5
            elif colors_similar or is_white:
                # If they are similar or primary is white, adjust for contrast
                bg_color = secondary
                text_color = get_contrasting_text_color(bg_color)  # This will give black for white backgrounds
                edge_color = 'black' if is_very_light else 'white'
                edge_width = 1.5
            else:
                # If they have good contrast between them
                text_color = primary
                edge_color = 'black' if colors_similar else secondary
                edge_width = 1.5 if colors_similar else 0.8
            # Add name without rotation
            ax.text(pos, header_y, display_name,
                   ha='center', va='bottom', 
                   fontsize=min(6.5, max(5, 8 - len(display_name) * 0.2)),
                   fontweight='bold', 
                   color=text_color,
                   bbox=dict(facecolor=secondary, 
                            edgecolor=edge_color,
                            boxstyle='round,pad=0.15',  # More compact padding
                            alpha=0.9,                             linewidth=edge_width))
    
    # Chart configuration - more space for title
    ax.set_title("Probability of finishing in each position", pad=40)
    ax.set_xlabel("Final position")
    ax.set_ylabel("Probability (%)")
    
    # Modify tick positions to center them correctly under the bars
    ax.set_xticks(positions)  # Use exactly the same positions where we draw the bars        
    ax.set_xticklabels([str(p) for p in positions])
    
    # Add vertical grid for better visualization of columns
    ax.grid(axis='x', linestyle='--', alpha=0.7)

    # Create legend manually with all teams
    legend_items = []
    for team in all_teams:
        position = current_positions.get(team, 999)
        points = current_points.get(team, 0)
        matches = current_matches.get(team, 0)
        # If the team has a patch (it should), use it for the legend
        if team in team_patches:
            # Include both points and matches played in legend label with total matches calculation
            legend_items.append((position, team_patches[team], f"{team} - {points} pts ({matches}/{total_matches})"))
    
    # Sort by current position
    legend_items.sort(key=lambda x: x[0])
    sorted_handles = [item[1] for item in legend_items]        
    sorted_labels = [item[2] for item in legend_items]
    
    ax.legend(sorted_handles, sorted_labels, title="Team (by current position)", 
              bbox_to_anchor=(1.05, 1), loc='upper left')
    
    # Adjust limits to give space to labels
    ax.set_ylim(0, 100)  # Limit to 100% to avoid white space above
    
    # Adjust spacing between bars and give more top margin for labels
    plt.subplots_adjust(bottom=0.15, top=0.85, left=0.05, right=0.85)
    plt.tight_layout()    
    plt.show()

def main():
    print("⚽ Available leagues:")
    for idx, (_, name) in LEAGUES.items():
        print(f"{idx}. {name}")
    try:
        choice = int(input("Select league number: "))
        
        # Handle custom ID option
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

    print(f"🔄 Initializing SofaScore for {league_name} (ID: {tournament_id})...")
    client = SofaScoreClient()
    
    print("🔄 Getting current season ID...")
    season_id = client.get_current_season_id(tournament_id)
    if not season_id:
        print("❌ Could not get season ID.")
        return

    print("🔄 Getting team colors...")
    team_colors = get_team_colors_from_standings(tournament_id, season_id)
    
    print("🔄 Getting overall league table...")
    base_table = client.get_league_table(tournament_id, season_id)
    if not base_table:
        print("❌ Could not get league table.")
        return
        
    print("🔄 Getting home league table...")
    home_table = client.get_home_league_table(tournament_id, season_id)
    if not home_table:
        print("⚠️ Could not get home league table. Using overall stats only.")
        home_table = None
        
    print("🔄 Getting away league table...")
    away_table = client.get_away_league_table(tournament_id, season_id)
    if not away_table:
        print("⚠️ Could not get away league table. Using overall stats only.")
        away_table = None

    print("🔄 Getting remaining fixtures...")
    fixtures = client.get_remaining_fixtures(tournament_id, season_id)
    if not fixtures:
        print("❌ Could not get remaining fixtures.")
        return
    
    # Show how many pending matches there are
    print(f"📅 Found {len(fixtures)} remaining matches to simulate in each iteration.")
    
    # Print the first 5 pending matches as a sample
    if fixtures and len(fixtures) > 0:
        print("\nExamples of remaining matches (first 5):")
        for i, match in enumerate(fixtures[:5], 1):
            print(f"{i}. {match['h']['title']} vs {match['a']['title']}")
        if len(fixtures) > 5:
            print(f"...and {len(fixtures) - 5} more matches\n")

    print("🔄 Simulating seasons...")
    position_counts = defaultdict(Counter)
    # List to store each simulated table as a tuple of teams
    all_simulated_tables = []
    
    start_time = time.time()  # Record start time
    simulations_completed = 0  # Counter for completed simulations
    
    # Use tqdm for progress bar with dynamic total
    progress_bar = tqdm(total=MAX_SIMULATIONS, desc="Simulation progress")
    
    # Run simulations until time limit or max count is reached
    while simulations_completed < MAX_SIMULATIONS:
        # Check elapsed time
        elapsed_time = time.time() - start_time
        if elapsed_time > MAX_SIMULATION_TIME_SECONDS:
            print(f"\n⏰ Time limit reached: {MAX_SIMULATION_TIME_SECONDS} seconds. Completed {simulations_completed} simulations.")
            break
        
        # Simulate all pending matches and get the resulting standings
        positions = simulate_season(base_table, fixtures, home_table, away_table)
        
        # Save the full table as a tuple (to be able to use it as a key in a dictionary)
        all_simulated_tables.append(tuple(positions))
        
        for pos, team in enumerate(positions, 1):
            position_counts[team][pos] += 1
            
        simulations_completed += 1
        progress_bar.update(1)
    
    progress_bar.close()
    
    if simulations_completed < MAX_SIMULATIONS:
        completion_percentage = (simulations_completed / MAX_SIMULATIONS) * 100
        print(f"⚠️ Only completed {completion_percentage:.1f}% of requested simulations due to time limit.")
    
    print(f"\n📈 Final simulation results ({simulations_completed} simulations):\n")
    
    # Improved formatting for console output
    max_team_name = max(len(team) for team in position_counts.keys()) + 15  # Increased for points and matches
    
    # Calculate total matches in a season
    total_teams = len([row for row in base_table if row[0] != 'Team'])
    total_matches = (total_teams - 1) * 2  # Each team plays against all others twice
    
    # Create current points and matches played dictionaries
    current_points = {}
    current_matches = {}
    for row in base_table[1:]:
        team_name = row[0]
        current_points[team_name] = int(row[7])
        current_matches[team_name] = int(row[1])
    
    # Function to format the probability outputs more clearly
    def format_prob_output(team, pos_counter):
        sorted_probs = sorted(pos_counter.items())
        
        points = current_points.get(team, 0)
        matches = current_matches.get(team, 0)
        team_with_info = f"{team} - {points} pts ({matches}/{total_matches})"
        
        # Show all positions individually with 3 significant digits
        grouped_probs = []
        
        for pos, count in sorted_probs:
            prob_pct = count / simulations_completed * 100
            
            # Format based on probability magnitude - using 3 significant digits
            if prob_pct >= 10:
                # Major probabilities: bold with 3 decimal places
                prob_text = f"Pos {pos}: {prob_pct:.3g}%"
                grouped_probs.append(f"\033[1m{prob_text}\033[0m")  # Bold for major probabilities
            elif prob_pct >= 1:
                # Medium probabilities: 3 decimal places, no emphasis
                prob_text = f"Pos {pos}: {prob_pct:.3g}%"
                grouped_probs.append(prob_text)
            else:
                # Minor probabilities (<1%): still show individually
                prob_text = f"Pos {pos}: {prob_pct:.3g}%"
                grouped_probs.append(prob_text)
        
        return f"{team_with_info:<{max_team_name}} │ " + "  ".join(grouped_probs)
    
    # Print the results - sorting by best probable position
    for team, pos_counter in sorted(position_counts.items(), 
                                   key=lambda x: min([p for p, c in x[1].items() if c/simulations_completed >= 0.05], 
                                                    default=999)):
        print(format_prob_output(team, pos_counter))
    
    # Find the most common standings
    table_counter = Counter(all_simulated_tables)
    most_common_table, most_common_count = table_counter.most_common(1)[0]
    most_common_percentage = (most_common_count / simulations_completed) * 100
    
    # Show the most common standings - IMPROVED VERSION
    print("\n\n📋 Most frequent standings as complete table:")
    print(f"(This exact standings occurs in {most_common_percentage:.3g}% of simulations)\n")
    print(f"{'Pos':<4}{'Team':<40}")
    print("─" * 44)
    
    # Show the full table that appears most frequently
    for pos, team in enumerate(most_common_table, 1):
        matches_played = current_matches.get(team, 0)
        
        # Add indicator for teams that have completed all their matches
        if matches_played >= total_matches:
            completed_indicator = " ✓"
        else:
            completed_indicator = ""
            
        print(f"{pos:<4}{team:<40}{completed_indicator}")
    
    # Make sure we call the visualization function with the actual number of simulations
    visualize_results(position_counts, simulations_completed, team_colors, base_table)

# Dixon-Coles model functions - NEWLY ADDED
def dixon_coles_tau(x, y, lambda_x, lambda_y, rho):
    """
    Función de corrección Dixon-Coles para partidos de fútbol.
    Corrige las probabilidades de Poisson en los resultados de pocos goles (0-0, 1-0, 0-1, 1-1).
    
    Args:
        x: Goles del equipo local
        y: Goles del equipo visitante
        lambda_x: Tasa esperada de goles del equipo local (λ_home)
        lambda_y: Tasa esperada de goles del equipo visitante (λ_away)
        rho: Parámetro de corrección (correlación entre resultados locales y visitantes)
        
    Returns:
        Factor de corrección τ
    """
    if x == 0 and y == 0:
        return 1 - lambda_x * lambda_y * rho
    elif x == 0 and y == 1:
        return 1 + lambda_x * rho
    elif x == 1 and y == 0:
        return 1 + lambda_y * rho
    elif x == 1 and y == 1:
        return 1 - rho
    else:
        return 1.0  # Sin corrección para otros resultados

def dixon_coles_probability(x, y, lambda_x, lambda_y, rho):
    """
    Calcula la probabilidad de un resultado específico usando el modelo Dixon-Coles.
    P(goles_local=x, goles_visitante=y) = Poisson(x;λ_x) * Poisson(y;λ_y) * τ(x,y,ρ)
    
    Args:
        x: Goles del equipo local
        y: Goles del equipo visitante
        lambda_x: Tasa esperada de goles del equipo local (λ_home)
        lambda_y: Tasa esperada de goles del equipo visitante (λ_away)
        rho: Parámetro de corrección (correlación entre resultados locales y visitantes)
        
    Returns:
        Probabilidad del resultado específico
    """
    # Probabilidad Poisson independiente para cada equipo
    p_x = np.exp(-lambda_x) * (lambda_x ** x) / math.factorial(x)
    p_y = np.exp(-lambda_y) * (lambda_y ** y) / math.factorial(y)
    
    # Aplicar corrección τ
    tau = dixon_coles_tau(x, y, lambda_x, lambda_y, rho)
    
    return p_x * p_y * tau

class DixonColesModel:
    """
    Implementación del modelo Dixon-Coles para simulación de partidos de fútbol
    que centraliza cálculos y parámetros con optimizaciones extremas de rendimiento.
    """
    
    def __init__(self, rho=-0.1, max_goals=8):
        """
        Inicializa el modelo Dixon-Coles con optimizaciones.
        
        Args:
            rho: Parámetro de correlación entre goles locales y visitantes (típicamente negativo)
            max_goals: Máximo número de goles a considerar en la matriz de probabilidad
        """
        self.rho = rho
        self.max_goals = max_goals
        self.home_lambdas = {}  # λ_home por equipo
        self.away_lambdas = {}  # λ_away por equipo
        self.global_lambdas = {}  # λ_global por equipo
        self.match_history = []  # Datos históricos para estimar rho
        
        # Precomputar matriz de probabilidades Poisson para mejorar rendimiento
        # Usar la versión ultra-optimizada con cálculos de logaritmos factoriales precomputados
        self.poisson_cache = precompute_poisson_matrix_optimized(max_lambda=5.0, lambda_step=0.02, max_goals=max_goals+5)
    
    def calculate_lambdas(self, base_table, home_table=None, away_table=None):
        """
        Calcula y centraliza todos los valores lambda a partir de las tablas.
        
        Args:
            base_table: Tabla general de la liga
            home_table: Tabla de partidos en casa (opcional)
            away_table: Tabla de partidos fuera (opcional)
        """
        # Limpiar lambdas previos
        self.home_lambdas.clear()
        self.away_lambdas.clear()
        self.global_lambdas.clear()
        
        # Calcular lambdas globales
        for row in base_table[1:]:  # Ignorar fila de cabecera
            team_name = row[0]
            matches = int(row[1])
            goals_for = int(row[5])
            
            # Calcular λ_global solo si han jugado partidos
            if matches > 0:
                self.global_lambdas[team_name] = goals_for / matches
            else:
                self.global_lambdas[team_name] = 1.0  # Valor por defecto
        
        # Calcular lambdas de local
        if home_table:
            for row in home_table[1:]:
                team_name = row[0]
                matches = int(row[1])
                goals_for = int(row[5])
                
                # Calcular λ_home solo si han jugado partidos en casa
                if matches > 0:
                    self.home_lambdas[team_name] = goals_for / matches
                else:
                    self.home_lambdas[team_name] = 1.0  # Valor por defecto
        
        # Calcular lambdas de visitante
        if away_table:
            for row in away_table[1:]:
                team_name = row[0]
                matches = int(row[1])
                goals_for = int(row[5])
                
                # Calcular λ_away solo si han jugado partidos fuera
                if matches > 0:
                    self.away_lambdas[team_name] = goals_for / matches
                else:
                    self.away_lambdas[team_name] = 1.0  # Valor por defecto
    
    def extract_match_history(self, base_table, home_table, away_table):
        """
        Extrae datos de partidos históricos para estimación de rho.
        Infiere resultados de partidos a partir de las tablas acumuladas.
        
        Args:
            base_table: Tabla general de la liga
            home_table: Tabla de partidos en casa
            away_table: Tabla de partidos fuera
        """
        self.match_history = []
        
        # Preparamos diccionarios para cada equipo
        teams = [row[0] for row in base_table[1:]]
        
        # Extraemos información histórica implícita en las tablas
        # Esta es una aproximación ya que no tenemos los resultados individuales
        for team in teams:
            if team in self.home_lambdas and team in self.away_lambdas:
                # Podemos simular un "partido promedio" para este equipo
                avg_home_goals = self.home_lambdas[team]
                
                # Para cada rival posible
                for opponent in teams:
                    if opponent != team and opponent in self.away_lambdas:
                        avg_away_goals = self.away_lambdas[opponent]
                        
                        # Añadimos un "partido representativo" con goles redondeados
                        # Esto es una aproximación ya que no tenemos los resultados exactos
                        self.match_history.append((
                            team,
                            opponent,
                            round(avg_home_goals),
                            round(avg_away_goals)
                        ))
    
    def estimate_rho(self):
        """
        Estima el parámetro ρ por máxima verosimilitud usando datos históricos.
        
        Returns:
            Valor óptimo de ρ estimado
        """
        if not self.match_history:
            print("⚠️ No hay suficientes datos históricos para estimar rho. Usando valor por defecto.")
            return self.rho
            
        def neg_log_likelihood(rho):
            """Función de log-verosimilitud negativa a minimizar."""
            nll = 0
            for h_team, a_team, h_goals, a_goals in self.match_history:
                if h_team in self.home_lambdas and a_team in self.away_lambdas:
                    lambda_h = self.home_lambdas[h_team]
                    lambda_a = self.away_lambdas[a_team]
                    
                    # Evitar valores no válidos
                    if lambda_h <= 0:
                        lambda_h = 0.1
                    if lambda_a <= 0:
                        lambda_a = 0.1
                        
                    # Calcular probabilidad con el modelo
                    p = self.dixon_coles_probability(h_goals, a_goals, lambda_h, lambda_a, rho)
                    
                    if p > 0:
                        nll -= np.log(p)
                    else:
                        nll += 100  # Penalización
                else:
                    nll += 50  # Penalización para equipos sin datos
            return nll
        
        # Optimización restringida al rango típico de rho en fútbol
        try:
            result = optimize.minimize_scalar(neg_log_likelihood, bounds=(-0.2, 0.2), method='bounded')
            
            if result.success:
                estimated_rho = result.x
                return estimated_rho
            else:
                print("⚠️ Optimización de rho falló. Usando valor por defecto.")
                return self.rho
        except Exception as e:
            print(f"⚠️ Error en estimación de rho: {e}. Usando valor por defecto.")
            return self.rho
    
    def tau(self, x, y, lambda_x, lambda_y, rho):
        """
        Función de corrección Dixon-Coles para resultados de pocos goles.
        
        Args:
            x: Goles del equipo local
            y: Goles del equipo visitante
            lambda_x: Tasa esperada de goles del equipo local
            lambda_y: Tasa esperada de goles del equipo visitante
            rho: Parámetro de correlación
        
        Returns:
            Factor de corrección τ
        """
        if x == 0 and y == 0:
            return 1 - lambda_x * lambda_y * rho
        elif x == 0 and y == 1:
            return 1 + lambda_x * rho
        elif x == 1 and y == 0:
            return 1 + lambda_y * rho
        elif x == 1 and y == 1:
            return 1 - rho
        else:
            return 1.0  # Sin corrección para otros resultados
    
    def dixon_coles_probability(self, x, y, lambda_x, lambda_y, rho):
        """
        Calcula la probabilidad de un resultado específico con el modelo Dixon-Coles.
        
        Args:
            x: Goles del equipo local
            y: Goles del equipo visitante
            lambda_x: Tasa esperada de goles del equipo local
            lambda_y: Tasa esperada de goles del equipo visitante
            rho: Parámetro de correlación
            
        Returns:
            Probabilidad del resultado específico
        """
        # Probabilidad Poisson independiente para cada equipo
        p_x = np.exp(-lambda_x) * (lambda_x ** x) / math.factorial(x)
        p_y = np.exp(-lambda_y) * (lambda_y ** y) / math.factorial(y)
        
        # Aplicar corrección τ
        tau = self.tau(x, y, lambda_x, lambda_y, rho)
        
        return p_x * p_y * tau
    
    def simulate_match(self, h_team, a_team, home_advantage=1.25, use_global=True):
        """
        Simula un partido usando el modelo Dixon-Coles ultra-optimizado.
        
        Args:
            h_team: Nombre del equipo local
            a_team: Nombre del equipo visitante
            home_advantage: Factor de ventaja local (>1 favorece al local)
            use_global: Si True, combina lambdas específicos con globales
            
        Returns:
            Tupla (goles_local, goles_visitante)
        """
        # Versión optimizada que usa la caché de distribuciones Poisson precomputadas
        return simulate_match_dixon_coles_optimized(
            h_team, a_team, 
            self.home_lambdas, self.away_lambdas, self.global_lambdas,
            self.rho, self.poisson_cache, 
            max_goals=self.max_goals, 
            home_advantage=home_advantage
        )
        
    def simulate_matches_parallel(self, matches, home_advantage=1.25):
        """
        Simula múltiples partidos en paralelo para máximo rendimiento.
        
        Args:
            matches: Lista de tuplas (h_team, a_team) o diccionarios con claves 'h' y 'a'
            home_advantage: Factor de ventaja local
            
        Returns:
            Lista de tuplas (goles_local, goles_visitante)
        """
        # Normalizar el formato de los partidos
        match_tuples = []
        for match in matches:
            if isinstance(match, dict) and 'h' in match and 'a' in match:
                h_team = match['h']['title'] if isinstance(match['h'], dict) else match['h']
                a_team = match['a']['title'] if isinstance(match['a'], dict) else match['a']
                match_tuples.append((h_team, a_team))
            else:
                match_tuples.append(match)  # Asumir que ya es una tupla (h_team, a_team)
        
        # Utilizar la función de simulación en paralelo
        return parallel_simulate_matches(
            match_tuples,
            self.home_lambdas, 
            self.away_lambdas, 
            self.global_lambdas,
            self.rho, 
            self.poisson_cache, 
            max_goals=self.max_goals, 
            home_advantage=home_advantage
        )

def precompute_poisson_matrix_optimized(max_lambda=5.0, lambda_step=0.02, max_goals=10):
    """
    Precompute a matrix of Poisson probabilities for performance optimization.

    Args:
        max_lambda: Maximum lambda value to consider.
        lambda_step: Step size for lambda values.
        max_goals: Maximum number of goals to consider.

    Returns:
        A dictionary with (lambda, goals) as keys and probabilities as values.
    """
    poisson_cache = {}
    lambdas = np.arange(0, max_lambda + lambda_step, lambda_step)
    for lam in lambdas:
        for goals in range(max_goals + 1):
            poisson_cache[(lam, goals)] = np.exp(-lam) * (lam ** goals) / math.factorial(goals)
    return poisson_cache

def get_nearest_lambda(value, step=0.02):
    """
    Snap a value to the nearest precomputed lambda in the Poisson cache.

    Args:
        value: The lambda value to snap.
        step: The step size used in the precomputed cache.

    Returns:
        The nearest lambda value.
    """
    return round(value / step) * step

def simulate_match_dixon_coles_optimized(h_team, a_team, home_lambdas, away_lambdas, global_lambdas, rho, poisson_cache, max_goals=8, home_advantage=1.25):
    """
    Simulate a match using the optimized Dixon-Coles model.

    Args:
        h_team: Home team name.
        a_team: Away team name.
        home_lambdas: Dictionary of home lambdas for teams.
        away_lambdas: Dictionary of away lambdas for teams.
        global_lambdas: Dictionary of global lambdas for teams.
        rho: Correlation parameter.
        poisson_cache: Precomputed Poisson probabilities.
        max_goals: Maximum number of goals to consider.
        home_advantage: Home advantage factor.

    Returns:
        Tuple of simulated goals (home_goals, away_goals).
    """
    lambda_home = home_lambdas.get(h_team, global_lambdas.get(h_team, 1.0)) * home_advantage
    lambda_away = away_lambdas.get(a_team, global_lambdas.get(a_team, 1.0))

    # Snap lambda values to the nearest precomputed key
    lambda_home = get_nearest_lambda(lambda_home)
    lambda_away = get_nearest_lambda(lambda_away)

    prob_matrix = np.zeros((max_goals + 1, max_goals + 1))
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            prob_matrix[x, y] = poisson_cache[(lambda_home, x)] * poisson_cache[(lambda_away, y)] * DixonColesModel.tau(None, x, y, lambda_home, lambda_away, rho)

    prob_matrix /= prob_matrix.sum()
    flat_index = np.random.choice(len(prob_matrix.flatten()), p=prob_matrix.flatten())
    home_goals = flat_index // (max_goals + 1)
    away_goals = flat_index % (max_goals + 1)

    return home_goals, away_goals

def parallel_simulate_matches(matches, home_lambdas, away_lambdas, global_lambdas, rho, poisson_cache, max_goals=8, home_advantage=1.25):
    """
    Simulate multiple matches in parallel using the optimized Dixon-Coles model.

    Args:
        matches: List of tuples (home_team, away_team).
        home_lambdas: Dictionary of home lambdas for teams.
        away_lambdas: Dictionary of away lambdas for teams.
        global_lambdas: Dictionary of global lambdas for teams.
        rho: Correlation parameter.
        poisson_cache: Precomputed Poisson probabilities.
        max_goals: Maximum number of goals to consider.
        home_advantage: Home advantage factor.

    Returns:
        List of tuples (home_goals, away_goals).
    """
    def simulate(match):
        h_team, a_team = match
        return simulate_match_dixon_coles_optimized(h_team, a_team, home_lambdas, away_lambdas, global_lambdas, rho, poisson_cache, max_goals, home_advantage)

    with ThreadPoolExecutor() as executor:
        results = list(executor.map(simulate, matches))

    return results

if __name__ == "__main__":
    main()
