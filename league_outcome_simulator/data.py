import time
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

class SofaScoreClient:
    """Client for fetching football data from SofaScore API."""
    
    BASE_URL = "https://api.sofascore.com/api/v1"

    def __init__(self, global_driver=None):
        # Cache for JSON responses to prevent duplicate requests
        self.json_cache = {}
        """Initialize SofaScore client with optional global driver reference."""
        self.driver = global_driver
        if not self.driver:
            self.setup_driver()
        # Storage for calculated team scoring rates
        self.team_lambdas = {
            'home': {},    # λ_home per team
            'away': {},    # λ_away per team
            'global': {},  # λ_global per team
        }
    
    def setup_driver(self):
        """Set up a new Selenium driver if not using global driver."""
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("user-agent=Mozilla/5.0")
        service = Service(log_path="nul")
        try:
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
        except Exception as e:
            print(f"❌ Error initializing Selenium driver: {e}")
            self.driver = None

    def fetch_json(self, url):
        """Fetch JSON data from a URL using Selenium."""
        if not self.driver:
            print("❌ Driver not initialized")
            return None
            
        # Check if we have cached data for this URL
        if url in self.json_cache:
            print(f"🔄 Using cached data for: {url}")
            return self.json_cache[url]
        
        print(f"🔄 Getting data from: {url}")
        try:
            self.driver.get(url)
            time.sleep(2)
            body = self.driver.find_element("tag name", "body").text
            # Parse and cache the JSON response
            json_data = json.loads(body)
            self.json_cache[url] = json_data
            return json_data
        except Exception as e:
            print(f"❌ Error fetching data: {e}")
            return None

    def get_current_season_id(self, tournament_id):
        """Get the current season ID for a tournament."""
        url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/seasons"
        data = self.fetch_json(url)
        if data and 'seasons' in data:
            seasons = sorted(data['seasons'], key=lambda x: x['id'], reverse=True)
            return seasons[0]['id'] if seasons else None
        return None
        
    def get_league_table(self, tournament_id, season_id):
        """
        Get overall league standings and calculate λ_global for each team.
        λ_global = total goals scored / matches played
        """
        url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/standings/total"
        data = self.fetch_json(url)
        if data and 'standings' in data and data['standings']:
            rows = [['Team', 'M', 'W', 'D', 'L', 'G', 'GA', 'PTS']]
            
            # Calculate global lambda for each team
            for row in data['standings'][0]['rows']:
                team_name = row['team']['name']
                matches = row['matches']
                goals_for = row['scoresFor']
                
                # Calculate λ_global only if matches have been played
                if matches > 0:
                    self.team_lambdas['global'][team_name] = goals_for / matches
                else:
                    # Default value for teams without matches
                    self.team_lambdas['global'][team_name] = 1.0
                
                rows.append([
                    team_name,
                    matches,
                    row['wins'],
                    row['draws'],
                    row['losses'],
                    goals_for,
                    row['scoresAgainst'],
                    row['points'],
                ])
            return rows
        return None
        
    def get_home_league_table(self, tournament_id, season_id):
        """
        Get home-only league standings and calculate λ_home for each team.
        λ_home = home goals scored / home matches played
        """
        url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/standings/home"
        data = self.fetch_json(url)
        if data and 'standings' in data and data['standings']:
            rows = [['Team', 'M', 'W', 'D', 'L', 'G', 'GA', 'PTS']]
            
            # Calculate home lambda for each team
            for row in data['standings'][0]['rows']:
                team_name = row['team']['name']
                matches = row['matches']
                goals_for = row['scoresFor']
                
                # Calculate λ_home only if home matches have been played
                if matches > 0:
                    self.team_lambdas['home'][team_name] = goals_for / matches
                else:
                    # Default value for teams without home matches
                    self.team_lambdas['home'][team_name] = 1.0
                
                rows.append([
                    team_name,
                    matches,
                    row['wins'],
                    row['draws'],
                    row['losses'],
                    goals_for,
                    row['scoresAgainst'],
                    row['points'],
                ])
            return rows
        return None
        
    def get_away_league_table(self, tournament_id, season_id):
        """
        Get away-only league standings and calculate λ_away for each team.
        λ_away = away goals scored / away matches played
        """
        url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/standings/away"
        data = self.fetch_json(url)
        if data and 'standings' in data and data['standings']:
            rows = [['Team', 'M', 'W', 'D', 'L', 'G', 'GA', 'PTS']]
            
            # Calculate away lambda for each team
            for row in data['standings'][0]['rows']:
                team_name = row['team']['name']
                matches = row['matches']
                goals_for = row['scoresFor']
                
                # Calculate λ_away only if away matches have been played
                if matches > 0:
                    self.team_lambdas['away'][team_name] = goals_for / matches
                else:
                    # Default value for teams without away matches
                    self.team_lambdas['away'][team_name] = 1.0
                
                rows.append([
                    team_name,
                    matches,
                    row['wins'],
                    row['draws'],
                    row['losses'],
                    goals_for,
                    row['scoresAgainst'],
                    row['points'],
                ])
            return rows
        return None

    def get_remaining_fixtures(self, tournament_id, season_id):
        """
        Get all remaining fixtures for a tournament with pagination support.
        The API returns up to 30 events per page, so we need to iterate through 
        pages until we get fewer than 30 events or a 404 error.
        """
        all_fixtures = []
        page = 0
        
        print(f"📅 Fetching remaining fixtures (pagination enabled)...")
        
        while True:
            url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/events/next/{page}"
            print(f"    Getting page {page} of fixtures...")
            
            data = self.fetch_json(url)
            
            # Check if we got a valid response with events
            if not data or 'events' not in data or not data['events']:
                print(f"    No more fixtures found (reached page {page})")
                break
                
            # Process events from this page
            page_events = []
            for event in data['events']:
                if event['status']['type'] == 'notstarted':
                    page_events.append({
                        'id': event['id'],
                        'h': {'title': event['homeTeam']['name']},
                        'a': {'title': event['awayTeam']['name']},
                        'datetime': event['startTimestamp'],
                    })
            
            # Add events from this page to our collection
            all_fixtures.extend(page_events)
            
            # Check if we got less than 30 events (which means this is the last page)
            if len(data['events']) < 30:
                print(f"    End of fixtures reached (page {page} had {len(data['events'])} events)")
                break
                
            # Move to next page
            page += 1
        
        print(f"📊 Found {len(all_fixtures)} total remaining fixtures across {page+1} pages")
        return all_fixtures
    
    def get_team_colors(self, tournament_id, season_id):
        """Extract team colors from the standings endpoint."""
        url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/season/{season_id}/standings/total"
        data = self.fetch_json(url)
        team_colors = {}
        if not data or "standings" not in data or not data["standings"]:
            return team_colors
            
        default_blue = "#374df5"  # Common default color from API
        
        print("\n🎨 Loading team colors...")
        # Process all teams from the standings
        for standing in data.get("standings", []):
            for row in standing.get("rows", []):
                team = row.get("team", {})
                team_name = team.get("name")
                if not team_name:
                    continue
                # Extract colors from the team data
                team_colors_data = team.get("teamColors", {})
                primary_color = team_colors_data.get("primary")
                secondary_color = team_colors_data.get("secondary")
                
                # Store colors (will be processed later if needed)
                team_colors[team_name] = {
                    "primary": primary_color if primary_color and primary_color != default_blue else None,
                    "secondary": secondary_color if secondary_color and secondary_color != default_blue else None,
                }
        return team_colors

    def close(self):
        """Close the Selenium driver if it's not a shared global driver."""
        if self.driver:
            try:
                self.driver.quit()
                self.driver = None
            except:
                pass