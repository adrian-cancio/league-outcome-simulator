"""Legacy Python Dixon-Coles implementation kept for experimentation only."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .utils import get_nearest_lambda, precompute_poisson_matrix_optimized


class DixonColesModel:
    """Pure Python fallback model for experimentation and tests."""

    def __init__(self, rho: float = -0.1, max_goals: int = 8):
        self.rho = rho
        self.max_goals = max_goals
        self.home_lambdas: dict[str, float] = {}
        self.away_lambdas: dict[str, float] = {}
        self.global_lambdas: dict[str, float] = {}
        self.poisson_cache = precompute_poisson_matrix_optimized(
            max_lambda=5.0,
            lambda_step=0.02,
            max_goals=max_goals + 5,
        )

    def calculate_lambdas(self, base_table, home_table=None, away_table=None):
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
                self.home_lambdas[team_name] = (
                    goals_for / matches if matches > 0 else 1.0
                )

        if away_table:
            for row in away_table[1:]:
                team_name = row[0]
                matches = int(row[1])
                goals_for = int(row[5])
                self.away_lambdas[team_name] = (
                    goals_for / matches if matches > 0 else 1.0
                )

    def tau(self, x, y, lambda_x, lambda_y, rho):
        if x == 0 and y == 0:
            return 1 - lambda_x * lambda_y * rho
        if x == 0 and y == 1:
            return 1 + lambda_x * rho
        if x == 1 and y == 0:
            return 1 + lambda_y * rho
        if x == 1 and y == 1:
            return 1 - rho
        return 1.0

    def simulate_match(self, h_team, a_team, home_advantage: float = 1.25):
        lambda_home = self.home_lambdas.get(
            h_team, self.global_lambdas.get(h_team, 1.0)
        )
        lambda_home *= home_advantage
        lambda_away = self.away_lambdas.get(
            a_team, self.global_lambdas.get(a_team, 1.0)
        )
        lambda_home = get_nearest_lambda(lambda_home)
        lambda_away = get_nearest_lambda(lambda_away)

        prob_matrix = np.zeros((self.max_goals + 1, self.max_goals + 1))
        for x in range(self.max_goals + 1):
            for y in range(self.max_goals + 1):
                p_x = self.poisson_cache[(lambda_home, x)]
                p_y = self.poisson_cache[(lambda_away, y)]
                prob_matrix[x, y] = (
                    p_x * p_y * self.tau(x, y, lambda_home, lambda_away, self.rho)
                )

        prob_matrix /= prob_matrix.sum()
        flat_index = np.random.choice(
            len(prob_matrix.flatten()), p=prob_matrix.flatten()
        )
        home_goals = flat_index // (self.max_goals + 1)
        away_goals = flat_index % (self.max_goals + 1)
        return home_goals, away_goals

    def simulate_matches_parallel(self, matches, home_advantage: float = 1.25):
        def simulate(match):
            return self.simulate_match(
                match["h"]["title"], match["a"]["title"], home_advantage
            )

        with ThreadPoolExecutor() as executor:
            return list(executor.map(simulate, matches))
