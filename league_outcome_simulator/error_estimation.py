"""Monte Carlo sampling error utilities."""

from __future__ import annotations

import numpy as np


def calculate_pp_error(position_counts, num_simulations, num_teams) -> float:
    """Calculate the average standard error in percentage points."""
    if num_simulations <= 0:
        return 0.0

    total_error_sum = 0.0
    num_probabilities_calculated = 0

    for team_positions in position_counts.values():
        for position in range(1, num_teams + 1):
            p = team_positions.get(position, 0) / num_simulations
            std_error_pp = np.sqrt(p * (1 - p) / num_simulations) * 100
            total_error_sum += float(std_error_pp)
            num_probabilities_calculated += 1

    if num_probabilities_calculated == 0:
        return 0.0
    return total_error_sum / num_probabilities_calculated
