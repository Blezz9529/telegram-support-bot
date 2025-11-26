# services/ai_agent.py
import os
import json
import logging
import subprocess
import sys
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ValidationError
import asyncio

# === Проверка и логирование установленных пакетов при старте ===
def log_installed_packages():
    try:
        result = subprocess.run([sys.executable, '-m', 'pip', 'list'], capture_output=True, text=True, timeout=10)
        logging.info("📦 Установленные пакеты:\n" + result.stdout[:2000] + ("..." if len(result.stdout) > 2000 else ""))
    except Exception as e:
        logging.warning(f"⚠️ Не удалось получить список пакетов: {e}")

log_installed_packages()


# === Импорты Google AI (без google.genai) ===
try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    _GOOGLE_AVAILABLE = True
except ImportError as e:
    logging.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА импорта google.generativeai: {e!r}")
    logging.exception("Детали импорта:")
    _GOOGLE_AVAILABLE = False

try:
    import imghdr
    _IMGHDR_AVAILABLE = True
except ImportError:
    _IMGHDR_AVAILABLE = False

logger = logging.getLogger(__name__)

# ✅ Модульный флаг анализа медиа
ENABLE_MEDIA_ANALYSIS = True


# === Схема ответа ===
class AgentResponse(BaseModel):
    action: str = Field(..., pattern=r"^(reply|collect_data|escalate)$")
    response_to_user: str
    detected_theme: Optional[str] = None
    data_collected: Dict[str, Any] = Field(default_factory=dict)
    missing_data: List[str] = Field(default_factory=list)
    escalation_reason: Optional[str] = None
    estimated_time: Optional[str] = None


# === Тематические правила ===
THEME_RULES = {
    "deposit": {
        "required_data": ["payment_proof"],
        "escalation_conditions": ["угроз", "суд", "полиц", "]()_
