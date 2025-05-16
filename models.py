import numpy as np
import math
from utils import precompute_poisson_matrix_optimized, get_nearest_lambda
from scipy import optimize
from concurrent.futures import ThreadPoolExecutor

class DixonColesModel:
    """Implementation of the Dixon-Coles model for football match simulation."""
    def __init__(self, rho=-0.1, max_goals=8):
        self.rho = rho
        self.max_goals = max_goals
        self.home_lambdas = {}
        self.away_lambdas = {}
        self.global_lambdas = {}
        self.match_history = []
        self.poisson_cache = precompute_poisson_matrix_optimized(max_lambda=5.0, lambda_step=0.02, max_goals=max_goals+5)

    def calculate_lambdas(self, base_table, home_table=None, away_table=None):
        """Calculate lambda values from league tables."""
        self.home_lambdas.clear()
        self.away_lambdas.clear()
        self.global_lambdas.clear()
        for row in base_table[1:]:
            team_name = row[0]
            matches = int(row[1])
            goals_for = int(row[5])
            self.global_lambdas[team_name] = goals_for / matches if matches > 0 else 1.0
        if home_table:
            for row in home_table[1:]:
                team_name = row[0]
                matches = int(row[1])
                goals_for = int(row[5])
                self.home_lambdas[team_name] = goals_for / matches if matches > 0 else 1.0
        if away_table:
            for row in away_table[1:]:
                team_name = row[0]
                matches = int(row[1])
                goals_for = int(row[5])
                self.away_lambdas[team_name] = goals_for / matches if matches > 0 else 1.0

    def simulate_match(self, h_team, a_team, home_advantage=1.25):
        """Simulate a match using the Dixon-Coles model."""
        lambda_home = self.home_lambdas.get(h_team, self.global_lambdas.get(h_team, 1.0)) * home_advantage
        lambda_away = self.away_lambdas.get(a_team, self.global_lambdas.get(a_team, 1.0))
        lambda_home = get_nearest_lambda(lambda_home)
        lambda_away = get_nearest_lambda(lambda_away)
        prob_matrix = np.zeros((self.max_goals + 1, self.max_goals + 1))
        for x in range(self.max_goals + 1):
            for y in range(self.max_goals + 1):
                prob_matrix[x, y] = self.poisson_cache[(lambda_home, x)] * self.poisson_cache[(lambda_away, y)]
        prob_matrix /= prob_matrix.sum()
        flat_index = np.random.choice(len(prob_matrix.flatten()), p=prob_matrix.flatten())
        home_goals = flat_index // (self.max_goals + 1)
        away_goals = flat_index % (self.max_goals + 1)
        return home_goals, away_goals

    def extract_match_history(self, base_table, home_table, away_table):
        """
        Extract historical match data for estimating the rho parameter.

        Args:
            base_table: Overall league standings table.
            home_table: Home-only league standings table.
            away_table: Away-only league standings table.
        """
        self.match_history = []
        teams = [row[0] for row in base_table[1:]]

        for team in teams:
            if team in self.home_lambdas and team in self.away_lambdas:
                avg_home_goals = self.home_lambdas[team]
                for opponent in teams:
                    if opponent != team and opponent in self.away_lambdas:
                        avg_away_goals = self.away_lambdas[opponent]
                        self.match_history.append((
                            team,
                            opponent,
                            round(avg_home_goals),
                            round(avg_away_goals)
                        ))

    def estimate_rho(self):
        """
        Estimate the rho parameter using historical match data.

        Returns:
            Estimated rho value.
        """
        if not self.match_history:
            return self.rho

        def negative_log_likelihood(rho):
            nll = 0
            for h_team, a_team, h_goals, a_goals in self.match_history:
                lambda_h = self.home_lambdas.get(h_team, 1.0)
                lambda_a = self.away_lambdas.get(a_team, 1.0)
                p = self.dixon_coles_probability(h_goals, a_goals, lambda_h, lambda_a, rho)
                nll -= np.log(p) if p > 0 else 100  # Penalize invalid probabilities
            return nll

        result = optimize.minimize_scalar(negative_log_likelihood, bounds=(-0.2, 0.2), method='bounded')
        return result.x if result.success else self.rho

    def dixon_coles_probability(self, x, y, lambda_x, lambda_y, rho):
        """
        Calculate the probability of a specific match result using the Dixon-Coles model.

        Args:
            x: Home team goals.
            y: Away team goals.
            lambda_x: Expected goals for the home team.
            lambda_y: Expected goals for the away team.
            rho: Correlation parameter.

        Returns:
            Probability of the result (x, y).
        """
        p_x = np.exp(-lambda_x) * (lambda_x ** x) / math.factorial(x)
        p_y = np.exp(-lambda_y) * (lambda_y ** y) / math.factorial(y)
        tau = self.tau(x, y, lambda_x, lambda_y, rho)
        return p_x * p_y * tau

    def tau(self, x, y, lambda_x, lambda_y, rho):
        """
        Dixon-Coles correction factor for low-scoring matches.

        Args:
            x: Home team goals.
            y: Away team goals.
            lambda_x: Expected goals for the home team.
            lambda_y: Expected goals for the away team.
            rho: Correlation parameter.

        Returns:
            Correction factor.
        """
        if x == 0 and y == 0:
            return 1 - lambda_x * lambda_y * rho
        elif x == 0 and y == 1:
            return 1 + lambda_x * rho
        elif x == 1 and y == 0:
            return 1 + lambda_y * rho
        elif x == 1 and y == 1:
            return 1 - rho
        return 1.0

    def simulate_matches_parallel(self, matches, home_advantage=1.25):
        """
        Simulate multiple matches in parallel using the Dixon-Coles model.

        Args:
            matches: List of dictionaries with 'h' (home team) and 'a' (away team).
            home_advantage: Factor for home team advantage.

        Returns:
            List of tuples (home_goals, away_goals) for each match.
        """
        def simulate(match):
            h_team = match['h']['title']
            a_team = match['a']['title']
            return self.simulate_match(h_team, a_team, home_advantage)

        with ThreadPoolExecutor() as executor:
            results = list(executor.map(simulate, matches))

        return results