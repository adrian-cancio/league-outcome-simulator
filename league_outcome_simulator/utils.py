"""Utility helpers shared across CLI, data loading and reporting."""

from __future__ import annotations

import json
import math
import os
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable


TEAM_ROW_HEADER = ["Team", "M", "W", "D", "L", "G", "GA", "PTS"]


def slugify(value: str) -> str:
    """Create a stable CLI-friendly slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "league"


def sanitize_path_segment(value: str) -> str:
    """Make a string safe for path segments."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "run"


def is_headless_environment() -> bool:
    """Best-effort check for non-GUI environments."""
    if sys.platform.startswith("win"):
        return False
    return not any(
        key in os.environ for key in ("DISPLAY", "WAYLAND_DISPLAY", "MIR_SOCKET")
    )


def ensure_json_serializable(value):
    """Convert counters and paths to JSON-friendly objects."""
    if isinstance(value, Counter):
        return dict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: ensure_json_serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [ensure_json_serializable(v) for v in value]
    return value


def write_json(path: Path, payload: dict) -> None:
    """Write a JSON file with predictable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(ensure_json_serializable(payload), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def get_color_luminance(hex_color: str) -> float:
    """Calculate the perceived brightness of a color (0-255)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return 0.299 * r + 0.587 * g + 0.114 * b


def get_contrasting_text_color(hex_color: str) -> str:
    """Return black or white depending on which has better contrast."""
    return "#000000" if get_color_luminance(hex_color) > 128 else "#FFFFFF"


def are_colors_similar(color1: str, color2: str, threshold: int = 60) -> bool:
    """Check if two colors are similar based on RGB distance."""
    c1 = color1.lstrip("#")
    c2 = color2.lstrip("#")
    r1, g1, b1 = int(c1[0:2], 16), int(c1[2:4], 16), int(c1[4:6], 16)
    r2, g2, b2 = int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16)
    distance = ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5
    return distance < threshold


def darken_color(hex_color: str, factor: float = 0.7) -> str:
    """Darken a color by multiplying RGB components by factor."""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    r = max(0, min(255, int(r * factor)))
    g = max(0, min(255, int(g * factor)))
    b = max(0, min(255, int(b * factor)))
    return f"#{r:02x}{g:02x}{b:02x}"


def deterministic_hex_color(team_name: str) -> str:
    """Generate a deterministic color based on team name."""
    name_hash = sum(ord(c) * (i + 1) for i, c in enumerate(team_name))
    r = (name_hash * 123) % 256
    g = (name_hash * 457) % 256
    b = (name_hash * 789) % 256
    if max(r, g, b) < 100:
        brightest = max(r, g, b)
        if brightest > 0:
            factor = 100 / brightest
            r = min(255, int(r * factor))
            g = min(255, int(g * factor))
            b = min(255, int(b * factor))
        else:
            r, g, b = 120, 120, 120
    return f"#{r:02x}{g:02x}{b:02x}"


def deterministic_secondary_color(team_name: str, primary_color: str) -> str:
    """Generate a secondary deterministic color."""
    primary = primary_color.lstrip("#")
    r = int(primary[0:2], 16)
    g = int(primary[2:4], 16)
    b = int(primary[4:6], 16)
    name_hash = sum(ord(c) for c in team_name)

    if name_hash % 4 == 0:
        r2, g2, b2 = 255 - r, 255 - g, 255 - b
    elif name_hash % 4 == 1:
        r2, g2, b2 = b, r, g
    elif name_hash % 4 == 2:
        factor = 0.6 if (r + g + b) > 380 else 1.7
        r2 = min(255, max(0, int(r * factor)))
        g2 = min(255, max(0, int(g * factor)))
        b2 = min(255, max(0, int(b * factor)))
    else:
        mix_hash = (name_hash * 37) % 256
        r2 = (r + mix_hash) % 256
        g2 = (g + mix_hash) % 256
        b2 = (b + mix_hash) % 256

    return f"#{r2:02x}{g2:02x}{b2:02x}"


def is_good_contrast(color1: str, color2: str, threshold: int = 120) -> bool:
    """Check if two colors have sufficient contrast."""
    c1 = color1.lstrip("#")
    c2 = color2.lstrip("#")
    r1, g1, b1 = int(c1[0:2], 16), int(c1[2:4], 16), int(c1[4:6], 16)
    r2, g2, b2 = int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16)
    brightness1 = (r1 * 299 + g1 * 587 + b1 * 114) / 1000
    brightness2 = (r2 * 299 + g2 * 587 + b2 * 114) / 1000
    brightness_diff = abs(brightness1 - brightness2)
    color_diff = abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)
    return brightness_diff > 70 or color_diff > threshold


def process_team_colors(
    team_colors: dict[str, dict[str, str | None]],
) -> dict[str, dict[str, str]]:
    """Fill in missing team colors deterministically."""
    processed_colors: dict[str, dict[str, str]] = {}
    for team_name, colors in team_colors.items():
        primary = colors.get("primary") or deterministic_hex_color(team_name)
        secondary = colors.get("secondary") or deterministic_secondary_color(
            team_name, primary
        )
        if not is_good_contrast(primary, secondary):
            secondary = deterministic_secondary_color(f"{team_name} alt", primary)
        processed_colors[team_name] = {"primary": primary, "secondary": secondary}
    return processed_colors


def format_duration(seconds: float) -> str:
    """Format a duration into a human-readable string."""
    total_ms = int(round(seconds * 1000))
    milliseconds = total_ms % 1000
    total_seconds = total_ms // 1000
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds_only = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} {'day' if days == 1 else 'days'}")
    if hours:
        parts.append(f"{hours} {'hour' if hours == 1 else 'hours'}")
    if minutes:
        parts.append(f"{minutes} {'minute' if minutes == 1 else 'minutes'}")
    if seconds_only or not parts:
        parts.append(f"{seconds_only} {'second' if seconds_only == 1 else 'seconds'}")
    if milliseconds:
        parts.append(
            f"{milliseconds} {'millisecond' if milliseconds == 1 else 'milliseconds'}"
        )
    return ", ".join(parts)


def extract_team_names(base_table: list[list]) -> list[str]:
    """Return team names from standings-like table."""
    return [row[0] for row in base_table[1:]]


def team_stats_from_table(base_table: list[list]) -> dict[str, dict[str, int]]:
    """Create a dict keyed by team with common stats."""
    stats = {}
    for position, row in enumerate(base_table[1:], start=1):
        stats[row[0]] = {
            "position": position,
            "matches": int(row[1]),
            "wins": int(row[2]),
            "draws": int(row[3]),
            "losses": int(row[4]),
            "gf": int(row[5]),
            "ga": int(row[6]),
            "points": int(row[7]),
        }
    return stats


def sort_table_rows(rows: list[list]) -> list[list]:
    """Sort table rows using common football tie-breakers."""
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            -int(row[7]),
            -(int(row[5]) - int(row[6])),
            -int(row[5]),
            row[0].lower(),
        ),
    )
    return [TEAM_ROW_HEADER.copy(), *sorted_rows]


def build_table_from_team_stats(team_stats: dict[str, dict[str, int]]) -> list[list]:
    """Create a standings table from team stats dict."""
    rows = []
    for team, stats in team_stats.items():
        rows.append(
            [
                team,
                stats["matches"],
                stats["wins"],
                stats["draws"],
                stats["losses"],
                stats["gf"],
                stats["ga"],
                stats["points"],
            ]
        )
    return sort_table_rows(rows)


def apply_result_to_team_stats(
    team_stats: dict[str, dict[str, int]],
    home_team: str,
    away_team: str,
    home_goals: int,
    away_goals: int,
) -> None:
    """Apply a finished fixture to a mutable team stats dict."""
    home = team_stats[home_team]
    away = team_stats[away_team]
    home["matches"] += 1
    away["matches"] += 1
    home["gf"] += home_goals
    home["ga"] += away_goals
    away["gf"] += away_goals
    away["ga"] += home_goals

    if home_goals > away_goals:
        home["wins"] += 1
        home["points"] += 3
        away["losses"] += 1
    elif home_goals < away_goals:
        away["wins"] += 1
        away["points"] += 3
        home["losses"] += 1
    else:
        home["draws"] += 1
        away["draws"] += 1
        home["points"] += 1
        away["points"] += 1


def apply_result_to_split_tables(
    base_stats: dict[str, dict[str, int]],
    home_stats: dict[str, dict[str, int]],
    away_stats: dict[str, dict[str, int]],
    home_team: str,
    away_team: str,
    home_goals: int,
    away_goals: int,
) -> None:
    """Apply a finished fixture to total, home-only and away-only tables."""
    apply_result_to_team_stats(base_stats, home_team, away_team, home_goals, away_goals)

    home = home_stats[home_team]
    home["matches"] += 1
    home["gf"] += home_goals
    home["ga"] += away_goals

    away = away_stats[away_team]
    away["matches"] += 1
    away["gf"] += away_goals
    away["ga"] += home_goals

    if home_goals > away_goals:
        home["wins"] += 1
        home["points"] += 3
        away["losses"] += 1
    elif home_goals < away_goals:
        away["wins"] += 1
        away["points"] += 3
        home["losses"] += 1
    else:
        home["draws"] += 1
        away["draws"] += 1
        home["points"] += 1
        away["points"] += 1


def clone_table(table: list[list] | None) -> list[list] | None:
    """Deep copy table-like nested lists."""
    if table is None:
        return None
    return [list(row) for row in table]


def fixture_label(home_team: str, away_team: str) -> str:
    """Human-friendly fixture identifier."""
    return f"{home_team} vs {away_team}"


def normalize_team_name(name: str) -> str:
    """Loose normalization for user-facing team matching."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def choose_team_name(raw_name: str, candidates: Iterable[str]) -> str:
    """Resolve a user-provided team name against known candidates."""
    normalized = normalize_team_name(raw_name)
    exact = [
        candidate
        for candidate in candidates
        if normalize_team_name(candidate) == normalized
    ]
    if exact:
        return exact[0]
    partial = [
        candidate
        for candidate in candidates
        if normalized in normalize_team_name(candidate)
    ]
    if len(partial) == 1:
        return partial[0]
    raise ValueError(f"Could not resolve team name '{raw_name}'")


def parse_score_string(value: str) -> tuple[int, int]:
    """Parse a score like '2-1' or '2:1'."""
    match = re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", value)
    if not match:
        raise ValueError(f"Invalid score '{value}'. Use formats like 2-1.")
    return int(match.group(1)), int(match.group(2))


def stable_seed_from_text(value: str) -> int:
    """Generate a deterministic seed from text."""
    return sum((i + 1) * ord(char) for i, char in enumerate(value)) % (2**32)


def random_hex_color() -> str:
    """Generate a random readable color."""
    r = random.randint(0, 255)
    g = random.randint(0, 255)
    b = random.randint(0, 255)
    while max(r, g, b) < 100:
        r = random.randint(0, 255)
        g = random.randint(0, 255)
        b = random.randint(0, 255)
    return f"#{r:02x}{g:02x}{b:02x}"


def summarize_probability_matrix(
    position_counts: dict[str, Counter], num_simulations: int
) -> list[dict[str, object]]:
    """Create a structured probability summary for JSON export."""
    summary = []
    for team, pos_counter in position_counts.items():
        summary.append(
            {
                "team": team,
                "positions": {
                    int(pos): (count / num_simulations if num_simulations else 0.0)
                    for pos, count in sorted(pos_counter.items())
                },
            }
        )
    return summary


def precompute_poisson_matrix_optimized(
    max_lambda: float = 5.0,
    lambda_step: float = 0.02,
    max_goals: int = 10,
) -> dict[tuple[float, int], float]:
    """Precompute Poisson probabilities for the legacy Python model."""
    poisson_cache: dict[tuple[float, int], float] = {}
    current = 0.0
    while current <= max_lambda + 1e-9:
        rounded = round(current, 2)
        for goals in range(max_goals + 1):
            poisson_cache[(rounded, goals)] = (
                math.exp(-rounded) * (rounded**goals) / math.factorial(goals)
            )
        current += lambda_step
    return poisson_cache


def get_nearest_lambda(value: float, step: float = 0.02) -> float:
    """Snap a value to the nearest precomputed lambda."""
    return round(round(value / step) * step, 2)
