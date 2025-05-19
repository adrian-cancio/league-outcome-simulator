"""
Statistical models for football match predictions.
"""
import numpy as np
import math
from scipy import optimize
from concurrent.futures import ThreadPoolExecutor
from utils import precompute_poisson_matrix_optimized, get_nearest_lambda

class DixonColesModel:
    """Implementation of the Dixon-Coles model for football match simulation."""
    
    def __init__(self, rho=-0.1, max_goals=8):
        """
        Initialize the Dixon-Coles model.
        
        Args:
            rho: Correlation parameter between home and away goals (-0.1 is a typical value)
            max_goals: Maximum number of goals to consider in probability calculations
        """
        self.rho = rho
        self.max_goals = max_goals
        self.home_lambdas = {}
        self.away_lambdas = {}
        self.global_lambdas = {}
        self.match_history = []
        self.poisson_cache = precompute_poisson_matrix_optimized(max_lambda=5.0, lambda_step=0.02, max_goals=max_goals+5)

    def calculate_lambdas(self, base_table, home_table=None, away_table=None):
        """
        Calculate lambda values (scoring rates) from league tables.
        
        Args:
            base_table: Overall league standings table
            home_table: Home-only league standings table
            away_table: Away-only league standings table
        """
        self.home_lambdas.clear()
        self.away_lambdas.clear()
        self.global_lambdas.clear()
        
        # Calculate global lambda (scoring rate) for each team
        for row in base_table[1:]:  # Skip header row
            team_name = row[0]
            matches = int(row[1])
            goals_for = int(row[5])
            self.global_lambdas[team_name] = goals_for / matches if matches > 0 else 1.0
        
        # Calculate home lambda (home scoring rate) for each team
        if home_table:
            for row in home_table[1:]:
                team_name = row[0]
                matches = int(row[1])
                goals_for = int(row[5])
                self.home_lambdas[team_name] = goals_for / matches if matches > 0 else 1.0
        
        # Calculate away lambda (away scoring rate) for each team
        if away_table:
            for row in away_table[1:]:
                team_name = row[0]
                matches = int(row[1])
                goals_for = int(row[5])
                self.away_lambdas[team_name] = goals_for / matches if matches > 0 else 1.0

    def simulate_match(self, h_team, a_team, home_advantage=1.25):
        """
        Simulate a match using the Dixon-Coles model.
        
        Args:
            h_team: Home team name
            a_team: Away team name
            home_advantage: Factor for home team advantage (1.0 = neutral, higher = more advantage)
            
        Returns:
            Tuple of (home_goals, away_goals)
        """
        # Get team scoring rates, applying home advantage
        lambda_home = self.home_lambdas.get(h_team, self.global_lambdas.get(h_team, 1.0)) * home_advantage
        lambda_away = self.away_lambdas.get(a_team, self.global_lambdas.get(a_team, 1.0))
        
        # Snap to nearest precomputed lambda for efficiency
        lambda_home = get_nearest_lambda(lambda_home)
        lambda_away = get_nearest_lambda(lambda_away)
        
        # Calculate probability matrix for all possible score combinations
        prob_matrix = np.zeros((self.max_goals + 1, self.max_goals + 1))
        for x in range(self.max_goals + 1):
            for y in range(self.max_goals + 1):
                # Apply Dixon-Coles correction to Poisson probabilities
                p_x = self.poisson_cache[(lambda_home, x)]
                p_y = self.poisson_cache[(lambda_away, y)]
                tau = self.tau(x, y, lambda_home, lambda_away, self.rho)
                prob_matrix[x, y] = p_x * p_y * tau
        
        # Normalize probabilities to ensure they sum to 1
        prob_matrix /= prob_matrix.sum()
        
        # Sample from the probability distribution
        flat_index = np.random.choice(len(prob_matrix.flatten()), p=prob_matrix.flatten())
        home_goals = flat_index // (self.max_goals + 1)
        away_goals = flat_index % (self.max_goals + 1)
        
        return home_goals, away_goals

    def tau(self, x, y, lambda_x, lambda_y, rho):
        """
        Dixon-Coles correction factor for low-scoring matches.
        
        Args:
            x: Home team goals
            y: Away team goals
            lambda_x: Expected goals for the home team
            lambda_y: Expected goals for the away team
            rho: Correlation parameter
            
        Returns:
            Correction factor
        """
        if x == 0 and y == 0:
            return 1 - lambda_x * lambda_y * rho
        elif x == 0 and y == 1:
            return 1 + lambda_x * rho
        elif x == 1 and y == 0:
            return 1 + lambda_y * rho
        elif x == 1 and y == 1:
            return 1 - rho
        return 1.0  # No correction for higher scores

    def simulate_matches_parallel(self, matches, home_advantage=1.25):
        """
        Simulate multiple matches in parallel using the Dixon-Coles model.
        
        Args:
            matches: List of dictionaries with 'h' (home team) and 'a' (away team)
            home_advantage: Factor for home team advantage
            
        Returns:
            List of tuples (home_goals, away_goals) for each match
        """
        def simulate(match):
            h_team = match['h']['title']
            a_team = match['a']['title']
            return self.simulate_match(h_team, a_team, home_advantage)

        with ThreadPoolExecutor() as executor:
            results = list(executor.map(simulate, matches))

        return results