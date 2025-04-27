from dotenv import load_dotenv
load_dotenv()
import os

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("No TELEGRAM_BOT_TOKEN found in environment variables")

ADMIN_TELEGRAM_IDS_STR = os.getenv("DMIAN_TELEGRAM_IDS", "")
ADMIN_TELEGRAM_IDS = [int(admin_id.strip()) for admin_id in ADMIN_TELEGRAM_IDS_STR.split(',') if admin_id.strip().isdigit()]

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    raise ValueError("No ADMIN_PASSWORD found in environment variables")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./telegram_bot.db")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

SLYOT_CANCEL_TIMEOUT_SECONDS = int(os.getenv("SLYOT_CANCEL_TIMEOUT_SECONDS", 300))

# --- Global Bot State (Using bot_data for simplicity, consider Redis/DB for persistence) ---
# This flag determines if the bot sends tasks to users
BOT_ACTIVE_STATE_KEY = "is_bot_globally_active"