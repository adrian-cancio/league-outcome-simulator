"""
Football season simulation module - delegates all simulation to Rust implementation.
"""
try:
    from rust_sim import simulate_season as rust_simulate_season
except ImportError:
    print("❌ Rust simulation library could not be loaded!")
    print("   Please make sure the Rust library is compiled with 'cargo build --release'")
    raise ImportError("Rust simulation module is required but could not be imported")

def simulate_season(base_table, fixtures, home_table=None, away_table=None):
    """
    Simulate the remainder of the season based on current standings and fixtures.
    
    This is a wrapper around the Rust implementation for backward compatibility.
    All actual simulation logic is implemented in Rust for performance.
    
    Args:
        base_table: Current league standings table
        fixtures: Remaining matches to be played
        home_table: Home-only league standings (not currently used by Rust implementation)
        away_table: Away-only league standings (not currently used by Rust implementation)
        
    Returns:
        List of tuples representing the final league standings
    """
    # Pass home_table and away_table to Rust implementation
    return rust_simulate_season(base_table, fixtures, home_table, away_table)