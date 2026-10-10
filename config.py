import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
DB_PATH = os.getenv("DB_PATH", "data.db")
SESSION_PATH = os.getenv("SESSION_PATH", "sessions/user")

# Дефолтные лимиты (можно менять в Mini App)
DEFAULT_DAILY_LIMIT = 300
DEFAULT_DELAY_MIN = 30
DEFAULT_DELAY_MAX = 90
DEFAULT_COOLDOWN = 60
DEFAULT_REPEATS = 1
