# config.py
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID"))
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x.strip()]

# === Настройки ИИ ===
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash")
GEMINI_TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", "0.1"))
GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "768"))
GEMINI_RETRY_ATTEMPTS = int(os.getenv("GEMINI_RETRY_ATTEMPTS", "3"))
GEMINI_RETRY_DELAY_BASE = float(os.getenv("GEMINI_RETRY_DELAY_BASE", "2.0"))

# === Пути ===
PROMPTS_PATH = "locales/prompts.json"