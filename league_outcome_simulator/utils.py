"""
Utility functions for the football probability simulation project.
"""
import math
import random
import numpy as np

# Color utility functions
def get_color_luminance(hex_color):
    """Calculate the perceived brightness of a color (0-255)"""
    hex_color = hex_color.lstrip('#')
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
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
    distance = ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5
    return distance < threshold

def random_hex_color():
    """Generate a random hex color"""
    r = random.randint(0, 255)
    g = random.randint(0, 255)
    b = random.randint(0, 255)
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

# Model optimization functions
def precompute_poisson_matrix_optimized(max_lambda=5.0, lambda_step=0.02, max_goals=10):
    """Precompute a matrix of Poisson probabilities for performance optimization."""
    poisson_cache = {}
    lambdas = np.arange(0, max_lambda + lambda_step, lambda_step)
    for lam in lambdas:
        for goals in range(max_goals + 1):
            poisson_cache[(lam, goals)] = np.exp(-lam) * (lam ** goals) / math.factorial(goals)
    return poisson_cache

def get_nearest_lambda(value, step=0.02):
    """Snap a value to the nearest precomputed lambda in the Poisson cache."""
    return round(value / step) * step

# League and team processing functions
def process_team_colors(team_colors):
    """Process team colors, filling in missing colors with deterministic values."""
    processed_colors = {}
    
    for team_name, colors in team_colors.items():
        primary = colors.get("primary")
        secondary = colors.get("secondary")
        
        # Generate primary color if missing
        if not primary:
            primary = deterministic_hex_color(team_name)
            
        # Generate secondary color if missing
        if not secondary:
            secondary = deterministic_secondary_color(team_name, primary)
            
        # Ensure good contrast between colors
        if not is_good_contrast(primary, secondary):
            secondary = deterministic_secondary_color(team_name + "alt", primary)
            
        processed_colors[team_name] = {
            "primary": primary,
            "secondary": secondary
        }
        
    return processed_colors

def format_duration(seconds: float) -> str:
    """
    Format a duration given in seconds into a human-readable string.

    Args:
        seconds: Duration in seconds.

    Returns:
        A string expressing the duration in days, hours, minutes, and seconds.
    """
    # Convert to total milliseconds and split
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds_only = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} {'day' if days == 1 else 'days'}")
    if hours:
        parts.append(f"{hours} {'hour' if hours == 1 else 'hours'}")
    if minutes:
        parts.append(f"{minutes} {'minute' if minutes == 1 else 'minutes'}")
    # Include seconds if non-zero or if no larger units
    if seconds_only or not parts:
        parts.append(f"{seconds_only} {'second' if seconds_only == 1 else 'seconds'}")
    # Include milliseconds if any remain
    if ms:
        parts.append(f"{ms} {'millisecond' if ms == 1 else 'milliseconds'}")
    return ', '.join(parts)