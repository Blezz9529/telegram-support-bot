# config.py
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID", "0"))
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x.strip()]

# === Настройки ИИ ===
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash")
GEMINI_TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", "0.1"))
GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "768"))
GEMINI_RETRY_ATTEMPTS = int(os.getenv("GEMINI_RETRY_ATTEMPTS", "3"))
GEMINI_RETRY_DELAY_BASE = float(os.getenv("GEMINI_RETRY_DELAY_BASE", "2.0"))
AI_BATCH_WINDOW_SECONDS = int(os.getenv("AI_BATCH_WINDOW_SECONDS", "10"))
AI_OPERATOR_PAUSE_SECONDS = int(os.getenv("AI_OPERATOR_PAUSE_SECONDS", "120"))

# === Пути ===
PROMPTS_PATH = "locales/prompts.json"

# === Виджет ===
WIDGET_SESSION_TTL_HOURS = int(os.getenv("WIDGET_SESSION_TTL_HOURS", "24"))

# === API site_id <-> telegram_id ===
SITE_ID_API_URL = os.getenv("SITE_ID_API_URL", "https://mega-slot-bot.ru/get_id")
SITE_ID_API_TOKEN = os.getenv("SITE_ID_API_TOKEN", "")
SITE_ID_API_TIMEOUT = float(os.getenv("SITE_ID_API_TIMEOUT", "5"))
