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
        "escalation_conditions": ["угроз", "суд", "полиц", "жалоб", "мошенник", "кинули", "обман"]
    },
    "partnership": {
        "required_data": ["proposal"],
        "escalation_conditions": []
    },
    "default": {
        "required_data": [],
        "escalation_conditions": ["угроз", "суд"]
    }
}


# === Автоопределение MIME ===
def determine_mime_type(data: bytes, filename: str = "") -> str:
    if _IMGHDR_AVAILABLE:
        img_type = imghdr.what(None, data)
        if img_type:
            return f"image/{img_type}"
    ext_map = {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
        'gif': 'image/gif', 'bmp': 'image/bmp', 'pdf': 'application/pdf'
    }
    if filename:
        ext = filename.lower().split('.')[-1]
        return ext_map.get(ext, 'application/octet-stream')
    return 'application/octet-stream'


# === Инициализация модели ===
_gemini_model = None

def _get_gemini_model() -> Optional["genai.GenerativeModel"]:
    global _gemini_model
    if not _GOOGLE_AVAILABLE:
        return None

    if _gemini_model is None:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.error("❌ GEMINI_API_KEY не задан")
            return None

        try:
            genai.configure(api_key=api_key)
            _gemini_model = genai.GenerativeModel(
                model_name="gemini-2.0-flash",
                generation_config=GenerationConfig(
                    temperature=0.1,
                    top_p=0.95,
                    max_output_tokens=768,
                    response_mime_type="application/json"
                ),
                system_instruction="Ты — ИИ-агент поддержки. Отвечай ТОЛЬКО валидным JSON."
            )
            _gemini_model.generate_content("OK", generation_config={"max_output_tokens": 1})
            logger.info("✅ Gemini: модель gemini-2.0-flash инициализирована")
        except Exception as e:
            logger.error(f"❌ Gemini: ошибка инициализации: {e}")
            return None
    return _gemini_model


# === Формирование промпта ===
def _build_prompt(
    user_message: str,
    history: List[Dict[str, Any]],
    theme: str,
    user_id: int,
    has_media: bool = False
) -> str:
    rules = THEME_RULES.get(theme, THEME_RULES["default"])
    history_preview = "\n".join([
        f"{'👤' if h.get('from_user') else '🤖'}: {h.get('text', '')}"
        for h in history[-5:]
    ])

    base_prompt = f"""[КОНТЕКСТ]
USER_ID: {user_id}
Тема: {theme}
Требуемые данные: {rules['required_data']}
Триггеры эскалации: {rules['escalation_conditions']}

[ИНСТРУКЦИЯ]
1. Если есть триггеры → action="escalate"
2. Если пользователь отправил фото/документ → action="reply" (анализируй содержимое)
3. Если нет данных по required_data → action="collect_data"
4. estimated_time: "2 ч" для deposit, "1 ч" для partnership, "12 ч" иначе

[ФОРМАТ ОТВЕТА СТРОГО JSON]
{
  "action": "reply|collect_data|escalate",
  "response_to_user": "...",
  "detected_theme": "...",
  "data_collected": {...},
  "missing_data": [...],
  "escalation_reason": "...",
  "estimated_time": "..."
}

[ИСТОРИЯ]
{history_preview}

[НОВОЕ СООБЩЕНИЕ]
{user_message}"""

    if ENABLE_MEDIA_ANALYSIS and has_media:
        base_prompt += (
            "\n\nПользователь отправил медиа. Проанализируй его и ответь, соответствует ли оно теме. "
            "Если это скриншот — определи, содержит ли он: реквизиты, сумму, дату, логотип бота. "
            "Не выдумывай данные — если не видишь — пиши 'не удалось распознать'."
        )
    return base_prompt


# === Вызов модели с корректной обработкой медиа (без google.genai) ===
async def _call_gemini_with_contents(contents: List[Any]) -> Optional[AgentResponse]:
    model = _get_gemini_model()
    if not model:
        return None

    logger.info(f"📤 Gemini: промпт (полный):\n{contents}")

    try:
        if len(contents) == 2 and isinstance(contents[0], bytes):
            image_bytes, text = contents
            mime_type = determine_mime_type(image_bytes)
            logger.info(f"🖼️ Медиа: {len(image_bytes)} байт, MIME={mime_type}")

            try:
                image_part = genai.Part.from_data(
                    data=image_bytes,
                    mime_type=mime_type
                )
                final_contents = [image_part, text]
            except AttributeError:
                logger.warning("⚠️ Part.from_data недоступен — медиа проигнорировано")
                final_contents = [text]
        else:
            final_contents = contents

        response = await asyncio.to_thread(model.generate_content, final_contents)
        if not response or not response.text:
            logger.warning("❌ Gemini: пустой ответ")
            return None

        logger.info(f"📥 Gemini: ответ (полный):\n{response.text}")
        return AgentResponse.model_validate(json.loads(response.text.strip()))
    except Exception as e:
        logger.exception("💥 Gemini: ошибка вызова")
        return None


# === Fallback ===
def _fallback_response(user_message: str, theme: str) -> AgentResponse:
    text = (user_message or "").lower()
    rules = THEME_RULES.get(theme, THEME_RULES["default"])
    for trigger in rules["escalation_conditions"]:
        if trigger in text:
            return AgentResponse(
                action="escalate",
                response_to_user="Ваш запрос передан оператору.",
                escalation_reason=f"Триггер: {trigger}",
                estimated_time="2 часа" if theme == "deposit" else "12 часов"
            )
    if theme == "deposit":
        return AgentResponse(
            action="collect_data",
            response_to_user="Пожалуйста, пришлите любые документы, подтверждающие ваш запрос.",
            missing_data=["Любые доказательства"],
            estimated_time="2 часа"
        )
    return AgentResponse(
        action="reply",
        response_to_user="Спасибо за информацию! Оператор свяжется при необходимости.",
        estimated_time=""
    )


# === Основной вход ===
async def process_ticket(
    *,
    user_message: str,
    history: List[Dict[str, Any]],
    current_theme: Optional[str] = None,
    user_id: int,
    image_bytes: Optional[bytes] = None,
    filename: str = ""
) -> Dict[str, Any]:
    theme = current_theme or "default"
    logger.info(f"🆕 Запрос ИИ: user_id={user_id}, тема={theme}, сообщение='{user_message}'")

    contents = [user_message]
    if ENABLE_MEDIA_ANALYSIS and image_bytes:
        contents = [image_bytes, user_message]

    ai_result = await _call_gemini_with_contents(contents)
    if ai_result:
        return ai_result.model_dump()

    logger.warning("⚠️ Используется fallback-логика")
    return _fallback_response(user_message, theme).model_dump()
