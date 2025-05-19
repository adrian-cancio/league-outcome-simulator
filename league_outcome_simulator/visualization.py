"""
Visualization module for football probability simulations.
"""
import pandas as pd
import matplotlib.pyplot as plt
from collections import Counter
from .utils import (
    get_color_luminance, 
    are_colors_similar, 
    darken_color, 
    deterministic_hex_color, 
    deterministic_secondary_color, 
    get_contrasting_text_color, 
    is_good_contrast,
    process_team_colors
)

def visualize_results(position_counts, num_simulations, team_colors, base_table):
    """
    Visualize the simulation results with a stacked bar chart.
    
    Args:
        position_counts: Dictionary mapping team names to Counter objects with position frequencies
        num_simulations: Total number of simulations performed
        team_colors: Dictionary mapping team names to primary/secondary colors
        base_table: Current league standings table
    """
    print("\n📊 Showing results in chart...")
    
    # Process team colors to fill in any missing values
    team_colors = process_team_colors(team_colors)
    
    # Calculate total matches in a season
    total_teams = len([row for row in base_table if row[0] != 'Team'])  # Count all rows except header
    total_matches = (total_teams - 1) * 2  # Each team plays against all others twice
    
    # Create dictionaries from base_table
    current_positions = {}
    current_points = {}
    current_matches = {}  # Track matches played
    for i, row in enumerate(base_table[1:], 1):  # Skip header row
        team_name = row[0]
        current_positions[team_name] = i
        current_points[team_name] = int(row[7])  # Points are at index 7
        current_matches[team_name] = int(row[1])  # Matches are at index 1
    
    # Create the data for visualization
    data = []
    for team, pos_counter in position_counts.items():
        for pos, count in pos_counter.items():
            data.append({"Team": team, "Position": pos, "Probability": count / num_simulations * 100})
    df = pd.DataFrame(data)
    
    # Define hatching patterns and subtle patterns
    hatch_patterns = [
        '////', '....', 'xxxx', 'oooo', '||||', '++++', '\\\\\\\\', '----', '****',
        'xx..', '++..', '\\\\..', '//..', '||..', 'oo..',          # Combined patterns
        'x+x+', '\\/\\/\/', '|x|x', 'o-o-', '*/*/',                # Alternating patterns
        '//\\\\', 'xxoo', '++**', '||||||||',                      # Dense patterns
        '++\\\\', 'xx||', 'oo--', '**xx', '..||', 'oo\\\\',        # More combinations
        '///\\\\\\', '...---', 'xxx|||', 'ooo+++'                  # High contrast patterns
    ]
    
    subtle_patterns = [
        '.', '/', 'x', '+', '|', '-', '\\', '*',                  # Simple patterns
        '..', '//', 'xx', '++', '||', '--', '\\\\', '**', 'oo',   # Double density
        '.-.', '/-/', 'x-x', '+-+', '|-|', '-.-', '\\-\\',        # Alternate with dashes
        './.', '/./', 'x/x', '+/+', '|/|', '-/-', '\\/\\',        # Alternate with slashes
        '...', '///', 'xxx', '+++', '|||'                         # Triple density
    ]
    
    # Function to get consistent pattern index from team name
    def get_pattern_index(team_name, pattern_list):
        # Use a hash of the team name to get a consistent index
        # This ensures the same team always gets the same pattern
        name_hash = sum(ord(c) for c in team_name)
        return name_hash % len(pattern_list)

    # Create the figure
    fig, ax = plt.subplots(figsize=(14, 8))
    
    # Keep track of which teams have been added to the plot
    all_teams = list(position_counts.keys())
    team_patches = {}  # To store handles for each team for the legend
    
    # Process each position individually
    positions = sorted(df['Position'].unique())
    
    # First pass: Draw all bars and prepare data for labels
    team_labels = []  # List to store pending label information
    for position in positions:
        # Filter data only for this position
        position_data = df[df['Position'] == position]
        
        # Group and calculate probabilities
        prob_by_team = position_data.groupby('Team')['Probability'].sum().reset_index()
        # Add current league position to data for sorting
        prob_by_team['CurrentPosition'] = prob_by_team['Team'].apply(
            lambda team: current_positions.get(team, 999)
        )
        
        # Sort by probability (ascending) for this specific position
        prob_by_team = prob_by_team.sort_values('Probability', ascending=True)
        
        # Initialize base for this position's segments
        bottom = 0
        x_pos = position  # Center bars exactly at the integer position
        width = 0.8  # Bar width
        segments_to_highlight = []
        # Variable to store the team with highest probability for this position
        top_team = None
        top_prob = 0
        
        # For each team in order (by probability), draw its segment
        for _, row in prob_by_team.iterrows():
            team = row['Team']
            prob = row['Probability']
            # Save team with highest probability
            if prob > top_prob:
                top_prob = prob
                top_team = team
                
            # Get team colors
            primary = team_colors[team]["primary"]
            secondary = team_colors[team]["secondary"]
            fill_color = primary
            
            # Determine the pattern based on whether colors are the same or different
            if secondary != primary:
                pattern_idx = get_pattern_index(team, hatch_patterns)
                hatch = hatch_patterns[pattern_idx]
                edge_color = secondary
            else:
                pattern_idx = get_pattern_index(team, subtle_patterns)
                hatch = subtle_patterns[pattern_idx]
                edge_color = darken_color(primary, factor=0.5)
            
            # Draw this segment
            rect = ax.bar(x_pos, prob, width=width, bottom=bottom, color=fill_color, 
                          edgecolor=edge_color, linewidth=1.5, label="")
            rect[0].set_hatch(hatch)
            
            # Save this rectangle for the legend if we don't have it yet
            if team not in team_patches:
                team_patches[team] = rect[0]
            
            # Save segment information
            segments_to_highlight.append((rect[0], bottom, prob, team))
            
            # Update base for next segment
            bottom += prob
        
        # Store top team info for second pass
        if top_team and top_prob >= 5.0:
            # Find segment data for this team
            for segment, bottom_pos, height, team_name in segments_to_highlight:
                if team_name == top_team:
                    # Group all information we'll need for labeling
                    team_labels.append({
                        'team': team_name,
                        'position': position,
                        'top_y': bottom_pos + height,  # Top Y position of the bar
                        'probability': height,
                        'primary_color': team_colors[team_name]['primary'],
                        'secondary_color': team_colors[team_name]['secondary'],
                    })
        
        # Show percentages inside bars with sufficient height
        for segment, bottom_pos, height, team_name in segments_to_highlight:
            if height < 2.0:  # Minimum size to show percentage
                continue
                
            # Calculate central position of segment
            center_x = x_pos
            center_y = bottom_pos + height / 2
            
            # Get team colors
            team_primary = team_colors[team_name]["primary"]
            team_secondary = team_colors[team_name]["secondary"]
            
            # Check color properties
            is_very_light_primary = get_color_luminance(team_primary) > 200
            is_very_light_secondary = get_color_luminance(team_secondary) > 200
            colors_identical = team_primary == team_secondary
            colors_similar = are_colors_similar(team_primary, team_secondary, 40) if not colors_identical else True
            is_large_bar = height >= 5.0

            # Adjust for specific cases with identical or very similar colors
            if colors_identical:
                # If colors are identical, use automatic contrast
                bg_color = team_primary
                text_color = get_contrasting_text_color(bg_color)
                edge_color = 'black' if is_very_light_primary else 'white'
                edge_width = 1.0
            elif colors_similar:
                # If they are similar but not identical, adjust for contrast
                bg_color = team_secondary
                text_color = get_contrasting_text_color(bg_color)
                edge_color = 'black' if is_very_light_secondary else 'white'
                edge_width = 1.0
            else:
                # If they have good contrast between them
                text_color = team_primary
                bg_color = team_secondary
                edge_color = 'none'
                edge_width = 0
            
            # Define percentage text
            percent_text = f"{height:.1f}%"
            
            # For large or medium bars, add percentage with more visible text
            if is_large_bar:
                # Larger size for large bars
                font_size = min(8, max(6, height / 2))
                ax.text(center_x, center_y, percent_text, 
                        ha='center', va='center', fontsize=font_size, fontweight='bold',
                        color=text_color, 
                        bbox=dict(facecolor=bg_color, edgecolor=edge_color,
                                alpha=0.85, pad=0.2, boxstyle='round,pad=0.2,rounding_size=0.2', 
                                linewidth=edge_width))
            else:
                # For smaller bars, adaptable size but not too small
                compact_font_size = min(7, max(5.5, height / 2))
                ax.text(center_x, center_y, percent_text, 
                        ha='center', va='center', fontsize=compact_font_size, fontweight='bold',
                        color=text_color,
                        bbox=dict(facecolor=bg_color, edgecolor=edge_color,
                                alpha=0.85, pad=0.15, boxstyle='round,pad=0.15,rounding_size=0.1', 
                                linewidth=edge_width))

    # Create dictionary to keep only ONE team per position (the one with highest probability)
    top_team_by_position = {}
    for label_info in team_labels:
        position = label_info['position']
        
        # Only save the team with highest probability for each position
        if position in top_team_by_position:
            if label_info['probability'] > top_team_by_position[position]['probability']:
                top_team_by_position[position] = label_info
        else:
            top_team_by_position[position] = label_info
    
    # Fixed height for all labels (as a column header)
    header_y = 103  # Increased slightly to give more space to title

    # Improved function to abbreviate names
    def abbreviate_name(name, max_length=8):
        if len(name) <= max_length:
            return name
            
        # Try abbreviating using initials
        if ' ' in name:
            words = name.split()
            if len(words) == 2:
                # First word + initial of second
                if len(words[0]) > max_length - 2:
                    return words[0][:max_length-2] + "." + words[1][0] + "."
                else:
                    return words[0] + " " + words[1][0] + "."
            else:
                # For more words, use only initials except first
                first = words[0][:min(5, len(words[0]))]  # Limit first word to 5 characters
                rest = ''.join(w[0] + '.' for w in words[1:])
                return first + " " + rest
        
        # If no spaces, truncate
        return name[:max_length-2] + ".."

    # Group adjacent positions to verify spacing
    position_groups = []
    current_group = []
    for pos in sorted(top_team_by_position.keys()):
        if not current_group or pos - current_group[-1] == 1:  # Adjacent positions
            current_group.append(pos)
        else:
            if current_group:  # If group has elements
                position_groups.append(current_group)
            current_group = [pos]
    
    if current_group:  # Don't forget last group
        position_groups.append(current_group)
    
    # Place team names as headers, processing by groups
    for group in position_groups:
        # For groups of adjacent positions, adjust sizes and rotations
        if len(group) > 1:
            # More compact for large groups
            compact_mode = len(group) > 3
            font_sizes = {}
            rotations = {}
            
            # First pass: assign base sizes and detect conflicts
            for pos in group:
                info = top_team_by_position[pos]
                team_name = info['team']
                display_name = abbreviate_name(team_name, 7 if compact_mode else 8)
                # Smaller font size for all to avoid overlaps
                font_sizes[pos] = min(6.0, max(4.5, 8 - len(display_name) * 0.3))
                # Alternate rotation on adjacent positions
                rotations[pos] = 15 if pos % 2 == 0 else -15
            
            # Second pass: Actually place the names
            for pos in group:
                info = top_team_by_position[pos]
                team_name = info['team']
                primary = info['primary_color']
                secondary = info['secondary_color']    
                display_name = abbreviate_name(team_name, 7 if compact_mode else 8)
                
                # Check color contrast
                is_very_light = get_color_luminance(primary) > 240
                colors_similar = are_colors_similar(primary, secondary)
                colors_identical = primary == secondary
                
                # Adjust colors according to case
                if colors_identical:
                    # If colors are identical, use automatic contrast
                    bg_color = secondary
                    text_color = get_contrasting_text_color(bg_color)
                    edge_color = text_color  # Border same as text
                    edge_width = 1.0
                else:
                    # Use primary color for text, secondary for background
                    bg_color = secondary
                    text_color = primary
                    edge_color = text_color  # Border same as text
                    edge_width = 0.8
                
                # Add name with adjusted rotation and size
                ax.text(pos, header_y, display_name,
                       ha='center', va='bottom', 
                       fontsize=font_sizes[pos],
                       fontweight='bold', rotation=rotations[pos],
                       color=text_color,
                       bbox=dict(facecolor=secondary, edgecolor=edge_color,
                                boxstyle='round,pad=0.15',
                                alpha=0.9, 
                                linewidth=edge_width))
        else:
            # For isolated positions, use standard approach
            pos = group[0]
            info = top_team_by_position[pos]
            team_name = info['team'] 
            primary = info['primary_color']
            secondary = info['secondary_color']    
            display_name = abbreviate_name(team_name, 9)  # Slightly longer for isolated positions
            
            # Check color contrast
            is_very_light = get_color_luminance(primary) > 240
            colors_similar = are_colors_similar(primary, secondary)
            colors_identical = primary == secondary
            
            # Adjust colors according to case
            if colors_identical:
                # If colors are identical, use automatic contrast
                bg_color = secondary
                text_color = get_contrasting_text_color(bg_color)
                edge_color = 'black' if is_very_light else 'white'
                edge_width = 1.5
            elif colors_similar or is_very_light:
                # If they are similar or primary is white, adjust for contrast
                bg_color = secondary
                text_color = get_contrasting_text_color(bg_color)
                edge_color = 'black' if is_very_light else 'white'
                edge_width = 1.5
            else:
                # If they have good contrast between them
                text_color = primary
                edge_color = 'black' if colors_similar else secondary
                edge_width = 1.5 if colors_similar else 0.8
                
            # Add name without rotation
            ax.text(pos, header_y, display_name,
                   ha='center', va='bottom', 
                   fontsize=min(6.5, max(5, 8 - len(display_name) * 0.2)),
                   fontweight='bold', 
                   color=text_color,
                   bbox=dict(facecolor=secondary, 
                            edgecolor=edge_color,
                            boxstyle='round,pad=0.15',
                            alpha=0.9,
                            linewidth=edge_width))
    
    # Chart configuration
    ax.set_title("Probability of finishing in each position", pad=40)
    ax.set_xlabel("Final position")
    ax.set_ylabel("Probability (%)")
    
    # Modify tick positions to center them correctly under the bars
    ax.set_xticks(positions)    
    ax.set_xticklabels([str(p) for p in positions])
    
    # Add vertical grid for better visualization of columns
    ax.grid(axis='x', linestyle='--', alpha=0.7)

    # Create legend manually with all teams
    legend_items = []
    for team in all_teams:
        position = current_positions.get(team, 999)
        points = current_points.get(team, 0)
        matches = current_matches.get(team, 0)
        # If the team has a patch (it should), use it for the legend
        if team in team_patches:
            # Include both points and matches played in legend label with total matches calculation
            legend_items.append((position, team_patches[team], f"{team} - {points} pts ({matches}/{total_matches})"))
    
    # Sort by current position
    legend_items.sort(key=lambda x: x[0])
    sorted_handles = [item[1] for item in legend_items]        
    sorted_labels = [item[2] for item in legend_items]
    
    ax.legend(sorted_handles, sorted_labels, title="Team (by current position)", 
              bbox_to_anchor=(1.05, 1), loc='upper left')
    
    # Adjust limits to give space to labels
    ax.set_ylim(0, 100)  # Limit to 100% to avoid white space above
    
    # Adjust spacing between bars and give more top margin for labels
    plt.subplots_adjust(bottom=0.15, top=0.85, left=0.05, right=0.85)
    plt.tight_layout()    
    plt.show()

def print_simulation_results(position_counts, num_simulations, base_table):
    """
    Print the simulation results in a readable format.
    
    Args:
        position_counts: Dictionary mapping team names to Counter objects with position frequencies
        num_simulations: Total number of simulations performed
        base_table: Current league standings table
    """
    # Calculate current points, matches, and total matches from base_table
    current_points = {}
    current_matches = {}
    total_teams = len([row for row in base_table if row[0] != 'Team'])
    total_matches = (total_teams - 1) * 2  # Each team plays against all others twice

    for row in base_table[1:]:  # Skip header row
        team_name = row[0]
        current_points[team_name] = int(row[7])  # Points are at index 7
        current_matches[team_name] = int(row[1])  # Matches are at index 1

    # Print final simulation results
    print("\n📈 Final simulation results:")
    for team, pos_counter in position_counts.items():
        total_simulations = sum(pos_counter.values())
        probabilities = [f"Pos {pos}: {count / total_simulations * 100:.3f}%" for pos, count in sorted(pos_counter.items())]
        print(f"{team} - {current_points[team]} pts ({current_matches[team]}/{total_matches})\t│ {'  '.join(probabilities)}")

    # Determine most frequent finishing position for each team (modal position)
    modal_positions = {team: counter.most_common(1)[0][0] for team, counter in position_counts.items()}
    # Sort teams by modal position and tie-breaker by highest count
    sorted_modal = sorted(modal_positions.items(), key=lambda x: (x[1], -position_counts[x[0]][x[1]]))

    # Print most frequent classification as a complete table
    print("\n📋 Most frequent classification as a complete table:")
    print("Pos\tTeam")
    print("────────────────────────────────────────────")
    for pos, (team, team_pos) in enumerate(sorted_modal, 1):
        print(f"{pos}\t{team}")