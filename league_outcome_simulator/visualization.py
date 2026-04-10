"""Visualization and text rendering for simulation outputs."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .utils import (
    are_colors_similar,
    darken_color,
    format_duration,
    get_contrasting_text_color,
    process_team_colors,
    team_stats_from_table,
)


NUM_TOP_TABLES = 10
HATCH_PATTERNS = [
    "////",
    "....",
    "xxxx",
    "oooo",
    "||||",
    "++++",
    "\\\\",
    "----",
    "****",
    "xx..",
    "++..",
    "\\..",
    "//..",
    "||..",
    "oo..",
    "x+x+",
    "/-/",
    "|x|x",
    "o-o-",
    "*/*/",
    "//\\",
    "xxoo",
    "++**",
    "||||||||",
    "++\\",
    "xx||",
    "oo--",
    "**xx",
    "..||",
    "oo\\",
]
MEDIUM_HATCH_PATTERNS = [
    "//",
    "xx",
    "++",
    "||",
    "..",
    "\\\\",
    "oo",
    "**",
    "//..",
    "xx..",
    "++..",
    "||..",
    "oo..",
    "\\..",
]


def _pick_hatch(team: str, patterns: list[str], recent_hatches: set[str]) -> str:
    """Pick a deterministic hatch while avoiding recent collisions."""
    start_index = sum((index + 1) * ord(char) for index, char in enumerate(team)) % len(
        patterns
    )
    step = 7 if len(patterns) > 7 else 3
    for offset in range(len(patterns)):
        hatch = patterns[(start_index + offset * step) % len(patterns)]
        if hatch not in recent_hatches:
            return hatch
    return patterns[start_index]


def _build_team_styles(
    ordered_teams: list[str], team_colors: dict[str, dict[str, str]]
) -> dict[str, dict[str, str]]:
    """Assign visually distinct styles, prioritizing nearby teams in the table."""
    styles: dict[str, dict[str, str]] = {}
    recent_hatches: list[str] = []
    recent_primary_colors: list[str] = []
    large_league = len(ordered_teams) >= 18

    for team in ordered_teams:
        colors = team_colors.get(team, {"primary": "#4472C4", "secondary": "#1F3A6D"})
        primary = colors.get("primary", "#4472C4")
        secondary = colors.get("secondary", primary)
        similar_pair = secondary == primary or are_colors_similar(
            primary, secondary, threshold=55
        )
        similar_to_recent = any(
            are_colors_similar(primary, recent, threshold=85)
            for recent in recent_primary_colors[-3:]
        )

        use_strong_pattern = large_league or similar_pair or similar_to_recent
        pattern_bank = HATCH_PATTERNS if use_strong_pattern else MEDIUM_HATCH_PATTERNS
        hatch = _pick_hatch(team, pattern_bank, set(recent_hatches[-3:]))

        if similar_pair:
            edge_color = get_contrasting_text_color(primary)
            if edge_color == "#FFFFFF" and are_colors_similar(
                primary, "#ffffff", threshold=90
            ):
                edge_color = darken_color(primary, factor=0.35)
        else:
            edge_color = secondary

        styles[team] = {
            "facecolor": primary,
            "edgecolor": edge_color,
            "hatch": hatch,
        }
        recent_hatches.append(hatch)
        recent_primary_colors.append(primary)

    return styles


def _legend_layout(team_count: int) -> tuple[int, int, float, bool]:
    """Return legend columns, font size, right margin and compact mode."""
    if team_count <= 18:
        return 1, 10, 0.80, False
    if team_count <= 30:
        return 2, 8, 0.70, True
    return 3, 7, 0.58, True


def _legend_label(team: str, stats: dict[str, int], compact: bool) -> str:
    """Shorten legend labels when there are many teams."""
    if compact:
        return f"{team} - {stats['points']} pts"
    return f"{team} - {stats['points']} pts ({stats['matches']} played)"


def visualize_results(
    position_counts,
    num_simulations,
    team_colors,
    base_table,
    run_dir,
    *,
    show_plot: bool,
):
    """Render and optionally show a stacked bar chart."""
    import matplotlib

    if not show_plot:
        matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    import pandas as pd

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    team_colors = process_team_colors(team_colors)
    current_stats = team_stats_from_table(base_table)

    rows = []
    for team, pos_counter in position_counts.items():
        for position, count in sorted(pos_counter.items()):
            rows.append(
                {
                    "Team": team,
                    "Position": int(position),
                    "Probability": (count / num_simulations) * 100
                    if num_simulations
                    else 0.0,
                    "CurrentPosition": current_stats[team]["position"],
                }
            )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return None

    pivot = frame.pivot_table(
        index="Position",
        columns="Team",
        values="Probability",
        fill_value=0.0,
    )
    ordered_teams = [
        team
        for team, _ in sorted(
            current_stats.items(), key=lambda item: item[1]["position"]
        )
    ]
    pivot = pivot.reindex(columns=ordered_teams)

    legend_columns, legend_font_size, right_margin, compact_legend = _legend_layout(
        len(ordered_teams)
    )
    figure_width = 15 if legend_columns == 1 else 17 if legend_columns == 2 else 19
    figure_height = 9 if len(ordered_teams) <= 24 else 10
    fig, ax = plt.subplots(figsize=(figure_width, figure_height))
    bottom = None
    legend_handles = []
    team_styles = _build_team_styles(ordered_teams, team_colors)
    for team in ordered_teams:
        values = pivot[team]
        style = team_styles[team]
        label = _legend_label(team, current_stats[team], compact_legend)
        ax.bar(
            pivot.index,
            values,
            bottom=bottom,
            color=style["facecolor"],
            edgecolor=style["edgecolor"],
            hatch=style["hatch"],
            linewidth=1.2,
            width=0.8,
            zorder=3,
        )
        legend_handles.append(
            Patch(
                facecolor=style["facecolor"],
                edgecolor=style["edgecolor"],
                hatch=style["hatch"],
                linewidth=1.2,
                label=label,
            )
        )
        bottom = values if bottom is None else bottom + values

    ax.set_title("Probability of finishing in each position")
    ax.set_xlabel("Final position")
    ax.set_ylabel("Probability (%)")
    ax.set_xticks(list(pivot.index))
    ax.set_ylim(0, 100)
    ax.grid(axis="y", linestyle="--", alpha=0.4, zorder=0)
    ax.legend(
        handles=legend_handles,
        bbox_to_anchor=(1.02, 1),
        loc="upper left",
        title="Teams",
        ncol=legend_columns,
        fontsize=legend_font_size,
        title_fontsize=legend_font_size + 1,
        columnspacing=1.2,
        handlelength=2.2,
        handletextpad=0.8,
        borderaxespad=0.0,
    )
    fig.tight_layout(rect=(0, 0, right_margin, 1))

    image_file = run_dir / "probabilities.png"
    fig.savefig(image_file, dpi=300)
    if show_plot:
        plt.show()
    else:
        plt.close(fig)
    print(f"Saved chart to {image_file}")
    return image_file


def print_simulation_results(
    position_counts,
    num_simulations,
    base_table,
    table_counter,
    run_dir,
    elapsed_time,
    num_fixtures,
):
    """Print and persist simulation results in a readable format."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    txt_file = run_dir / "simulation.txt"

    current_stats = team_stats_from_table(base_table)
    summary_line = f"Iterations run: {num_simulations}, elapsed time: {format_duration(elapsed_time)}"
    stats_line_1 = (
        f"Avg iterations per second: {num_simulations / elapsed_time:.2f}"
        if elapsed_time
        else "Avg iterations per second: 0.00"
    )
    stats_line_2 = (
        f"Total matches simulated: {num_simulations * num_fixtures} "
        f"(matches per iteration: {num_fixtures})"
    )
    header = "Final simulation results:"

    print(summary_line)
    print(stats_line_1)
    print(stats_line_2)

    ordered_teams = [
        team
        for team, _ in sorted(
            current_stats.items(), key=lambda item: item[1]["position"]
        )
    ]
    prefixes = {
        team: (
            f"{team} - {current_stats[team]['points']} pts "
            f"({current_stats[team]['matches']} played)"
        )
        for team in ordered_teams
    }
    max_prefix_len = max(len(prefix) for prefix in prefixes.values())

    with txt_file.open("w", encoding="utf-8") as handle:
        handle.write(summary_line + "\n")
        handle.write(stats_line_1 + "\n")
        handle.write(stats_line_2 + "\n")
        handle.write(header + "\n")

        for team in ordered_teams:
            pos_counter = position_counts[team]
            total = sum(pos_counter.values())
            probs = [
                f"Pos {pos}: {count / total * 100:.3g}%"
                for pos, count in sorted(pos_counter.items())
            ]
            line = f"{prefixes[team].ljust(max_prefix_len)} | {'  '.join(probs)}"
            print(line)
            handle.write(line + "\n")

        top_header = f"Top {NUM_TOP_TABLES} full final tables (approximate ranking):"
        combined_header = (
            "Combined top candidates by position (from ranked top tables):"
        )
        print(top_header)
        handle.write(top_header + "\n")
        if table_counter:
            for index, (table, count) in enumerate(
                table_counter.most_common(NUM_TOP_TABLES),
                start=1,
            ):
                teams_line = ", ".join(
                    f"{position + 1}:{team}" for position, team in enumerate(table)
                )
                line = f"{index}. {teams_line} (score {count:.3f})"
                print(line)
                handle.write(line + "\n")
        else:
            print("No full table data available.")
            handle.write("No full table data available.\n")

        print(combined_header)
        handle.write(combined_header + "\n")
        top_tables = [table for table, _ in table_counter.most_common(NUM_TOP_TABLES)]
        if top_tables:
            for pos in range(len(top_tables[0])):
                candidates = Counter(table[pos] for table in top_tables)
                row = ", ".join(
                    f"{team} ({count / len(top_tables) * 100:.3g}%)"
                    for team, count in candidates.most_common()
                )
                line = f"Pos {pos + 1}: {row}"
                print(line)
                handle.write(line + "\n")

    print(f"Saved simulation results to {txt_file}")
    return txt_file
