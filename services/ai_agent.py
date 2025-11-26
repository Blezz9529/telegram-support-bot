# services/ai_agent.py
import os
import json
import logging
import asyncio
from typing import Any, Dict, List, Optional
try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False
    logging.warning("❗ google-generativeai не установлен")

try:
    import imghdr
    _IMGHDR_AVAILABLE = True
except ImportError:
    _IMGHDR_AVAILABLE = False

logger = logging.getLogger(__name__)
ENABLE_MEDIA_ANALYSIS = True


class AgentResponse:
    """Простая замена Pydantic-модели для fallback-совместимости"""
    def __init__(self, **kwargs):
        self.action = kwargs.get("action", "reply")
        self.response_to_user = kwargs.get("response_to_user", "")
        self.detected_theme = kwargs.get("detected_theme")
        self.data_collected = kwargs.get("data_collected", {})
        self.missing_data = kwargs.get("missing_data", [])
        self.escalation_reason = kwargs.get("escalation_reason")
        self.estimated_time = kwargs.get("estimated_time")


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


_gemini_model = None

def _get_gemini_model():
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


def _build_prompt(user_message, history, theme, user_id, has_media=False):
    rules = THEME_RULES.get(theme, THEME_RULES["default"])
    history_preview = "\n".join([
        f"{'👤' if h.get('from_user') else '🤖'}: {h.get('text', '')}"
        for h in history[-5:]
    ])
    return f"""[КОНТЕКСТ]
USER_ID: {user_id}
Тема: {theme}
Требуемые данные: {rules['required_data']}
Триггеры эскалации: {rules['escalation_conditions']}

[ИНСТРУКЦИЯ]
1. Если есть триггеры → action="escalate"
2. Если пользователь отправил фото/документ → action="reply"
3. Если нет данных по required_data → action="collect_data"
4. estimated_time: "2 ч" для deposit, "1 ч" для partnership, "12 ч" иначе

[ФОРМАТ ОТВЕТА СТРОГО JSON]
{{
  "action": "reply|collect_data|escalate",
  "response_to_user": "...",
  "detected_theme": "...",
  "data_collected": {{}},
  "missing_data": [],
  "escalation_reason": "...",
  "estimated_time": "..."
}}

[ИСТОРИЯ]
{history_preview}

[НОВОЕ СООБЩЕНИЕ]
{user_message}"""


async def _call_gemini_with_contents(contents):
    model = _get_gemini_model()
    if not model:
        return None

    logger.info(f"📤 Gemini: промпт (полный):\n{contents}")

    try:
        response = await asyncio.to_thread(model.generate_content, contents)
        if not response or not response.text:
            logger.warning("❌ Gemini: пустой ответ")
            return None

        logger.info(f"📥 Gemini: ответ (полный):\n{response.text}")
        return AgentResponse(**json.loads(response.text.strip()))
    except Exception as e:
        logger.exception("💥 Gemini: ошибка вызова")
        return None


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
    return AgentResponse(
        action="reply",
        response_to_user="Спасибо за информацию! Оператор свяжется при необходимости.",
        estimated_time=""
    )


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
        try:
            mime_type = determine_mime_type(image_bytes, filename)
            logger.info(f"🖼️ Медиа: {len(image_bytes)} байт, MIME={mime_type}")
            image_part = genai.Part.from_data(data=image_bytes, mime_type=mime_type)
            contents = [image_part, user_message]
        except Exception as e:
            logger.error(f"❌ Ошибка создания Part: {e}")

    ai_result = await _call_gemini_with_contents(contents)
    if ai_result:
        return {
            "action": ai_result.action,
            "response_to_user": ai_result.response_to_user,
            "detected_theme": ai_result.detected_theme,
            "data_collected": ai_result.data_collected,
            "missing_data": ai_result.missing_data,
            "escalation_reason": ai_result.escalation_reason,
            "estimated_time": ai_result.estimated_time
        }

    logger.warning("⚠️ Используется fallback-логика")
    fallback = _fallback_response(user_message, theme)
    return {
        "action": fallback.action,
        "response_to_user": fallback.response_to_user,
        "detected_theme": fallback.detected_theme,
        "data_collected": fallback.data_collected,
        "missing_data": fallback.missing_data,
        "escalation_reason": fallback.escalation_reason,
        "estimated_time": fallback.estimated_time
    }