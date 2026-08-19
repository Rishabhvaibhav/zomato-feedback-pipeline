"""All configurations kept in one single place only — Master config for Zomato raw data collection."""
import os
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent

DATA_DIR = PROJECT_ROOT / "data" / "raw"
TODAY = datetime.now().strftime("%Y-%m-%d")

MAX_RETRIES = 1
BASE_DELAY = 2.0
MAX_DELAY = 60.0
BACKOFF_FACTOR = 2.0
JITTER = True

DEFAULT_DELAY = 1.5
TRUSTPILOT_DELAY = 2.5
REDDIT_DELAY = 2.0
GOOGLE_PLAY_DELAY = 1.0

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

GOOGLE_NEWS_QUERIES = [
    "Zomato",
    "Zomato complaint",
]

TRUSTPILOT_BASE_URL = "https://www.trustpilot.com/review/zomato.in"
TRUSTPILOT_MAX_PAGES = 500

REDDIT_QUERIES = [
    "Zomato",
    "Zomato complaint",
]
REDDIT_SUBREDDITS = [
    "india",
]
REDDIT_MAX_POSTS_PER_QUERY = 1000
REDDIT_MAX_COMMENTS_PER_POST = 500

GOOGLE_PLAY_APP_ID = "com.application.zomato"
GOOGLE_PLAY_LANG = "en"
GOOGLE_PLAY_COUNTRY = "in"
GOOGLE_PLAY_MAX_REVIEWS = 9000
GOOGLE_PLAY_SORT = "newest"

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"collection_{TODAY}.log"


def setup_logging():
    """Setup live logging to both console and log file simultaneously with immediate flush."""
    import logging
    import sys

    root_logger = logging.getLogger()
    root_logger.setLevel(LOG_LEVEL)

    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    formatter = logging.Formatter(LOG_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(LOG_LEVEL)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a")
    file_handler.setLevel(LOG_LEVEL)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
