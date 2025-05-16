import time
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

class SofaScoreClient:
    BASE_URL = "https://api.sofascore.com/api/v1"

    def __init__(self):
        self.driver = None
        self.setup_driver()

    def setup_driver(self):
        """Set up the Selenium driver."""
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("user-agent=Mozilla/5.0")
        service = Service(log_path="nul")
        self.driver = webdriver.Chrome(service=service, options=chrome_options)

    def fetch_json(self, url):
        """Fetch JSON data from a URL using Selenium."""
        self.driver.get(url)
        time.sleep(2)
        body = self.driver.find_element("tag name", "body").text
        return json.loads(body)

    def get_current_season_id(self, tournament_id):
        url = f"{self.BASE_URL}/unique-tournament/{tournament_id}/seasons"
        data = self.fetch_json(url)
        if data and 'seasons' in data:
            seasons = sorted(data['seasons'], key=lambda x: x['id'], reverse=True)
            return seasons[0]['id'] if seasons else None
        return None