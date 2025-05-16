from models import DixonColesModel
try:
    from rust_sim import simulate_season as rust_simulate_season
except ImportError:
    rust_simulate_season = None

def simulate_season(base_table, fixtures, home_table=None, away_table=None):
    """Simulate the remainder of the season based on current standings and fixtures."""
    # Use Rust-accelerated simulation if available
    if rust_simulate_season is not None:
        return rust_simulate_season(base_table, fixtures)
    standings = {row[0]: {"PTS": int(row[7]), "GF": int(row[5]), "GA": int(row[6]), "M": int(row[1])} for row in base_table[1:]}
    dc_model = DixonColesModel(rho=-0.1, max_goals=8)
    dc_model.calculate_lambdas(base_table, home_table, away_table)
    match_results = [dc_model.simulate_match(match['h']['title'], match['a']['title']) for match in fixtures]
    for i, match in enumerate(fixtures):
        h_team = match['h']['title']
        a_team = match['a']['title']
        gh, ga = match_results[i]
        standings[h_team]["GF"] += gh
        standings[h_team]["GA"] += ga
        standings[h_team]["M"] += 1
        standings[a_team]["GF"] += ga
        standings[a_team]["GA"] += gh
        standings[a_team]["M"] += 1
        if gh > ga:
            standings[h_team]["PTS"] += 3
        elif ga > gh:
            standings[a_team]["PTS"] += 3
        else:
            standings[h_team]["PTS"] += 1
            standings[a_team]["PTS"] += 1
    final_standings = sorted(standings.items(), key=lambda x: (-x[1]["PTS"], -(x[1]["GF"] - x[1]["GA"]), -x[1]["GF"]))
    sorting_key = lambda x: (-x[1]["PTS"], -(x[1]["GF"] - x[1]["GA"]), -x[1]["GF"])
    return sorted(final_standings, key=sorting_key)