\
"""
Error estimation module for football probability simulations.
"""
import numpy as np

def calculate_pp_error(position_counts, num_simulations, num_teams):
    """
    Calculate the percentage points (pp) of error for the simulation.
    This is a simplified error estimation. For each team, it calculates the standard error
    of the mean for the probability of finishing in each position.
    The overall error is the average of these standard errors.

    Args:
        position_counts: Dictionary mapping team names to Counter objects with position frequencies.
        num_simulations: Total number of simulations performed.
        num_teams: Total number of teams in the league.

    Returns:
        float: The average percentage point error.
    """
    if num_simulations == 0:
        return 0.0

    total_error_sum = 0
    num_probabilities_calculated = 0

    for team_positions in position_counts.values():
        for position in range(1, num_teams + 1):
            # Probability of this team finishing in this position
            p = team_positions.get(position, 0) / num_simulations
            # Standard error of a proportion: sqrt(p * (1-p) / n)
            # Multiply by 100 to get percentage points
            if num_simulations > 0:
                std_error_pp = np.sqrt(p * (1 - p) / num_simulations) * 100
                total_error_sum += std_error_pp
                num_probabilities_calculated += 1
    
    if num_probabilities_calculated == 0:
        return 0.0

    average_pp_error = total_error_sum / num_probabilities_calculated
    return average_pp_error
