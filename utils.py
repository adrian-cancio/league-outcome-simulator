import math
import random
import numpy as np

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