# services/ai_agent.py
import os
import json
import logging
import asyncio
import subprocess
import sys
from typing import Any, Dict, List, Optional

# === Системные импорты ===
try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    _GOOGLE_AVAILABLE = True
except ImportError as e:
    _GOOGLE_AVAILABLE = False
    logging.critical(f"❌ ОШИБКА импорта google.generativeai: {e!r}")
    logging.exception("Детали:")

# === Доп. импорты (опционально) ===
try:
    import imghdr
    _IMGHDR_AVAILABLE = True
except ImportError:
    _IMGHDR_AVAILABLE = False

logger = logging.getLogger(__name__)

# ✅ Модульный флаг: включить/отключить анализ медиа
ENABLE_MEDIA_ANALYSIS = True


# === 1. Простая совместимая структура ответа (без strict Pydantic) ===
class AgentResponse:
    def __init__(self, **kwargs):
        self.action = kwargs.get("action", "reply")
        self.response_to_user = kwargs.get("response_to_user", "")
        self.detected_theme = kwargs.get("detected_theme")
        self.data_collected = kwargs.get("data_collected", {})
        self.missing_data = kwargs.get("missing_data", [])
        self.escalation_reason = kwargs.get("escalation_reason")
        self.estimated_time = kwargs.get("estimated_time")


# === 2. Тематические правила ===
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


# === 3. Определение MIME-типа с fallback'ом ===
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


# === 4. Инициализация модели ===
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
            _gemini_model.generate_content("OK", generation_config={"max_output_tokens":1})
            logger.info("✅ Gemini: модель gemini-2.0-flash инициализирована")
        except Exception as e:
            logger.error(f"❌ Gemini: ошибка инициализации: {e}")
            return None
    return _gemini_model


# === 5. Универсальный парсер ответа с fallback'ом ===
def parse_gemini_response(raw_text: str) -> AgentResponse:
    """
    Парсит ответ Gemini, даже если он:
      - обёрнут в {"agentResponse": {...}}
      - содержит префиксы [REPLY]/[COLLECT]/[ESCALATE]
      - невалидный JSON
    Всегда возвращает валидный AgentResponse.
    """
    # 1. Попытка JSON
    try:
        data = json.loads(raw_text.strip())
        if isinstance(data, dict) and "agentResponse" in data:
            data = data["agentResponse"]
        return AgentResponse(**{
            "action": str(data.get("action", "reply")),
            "response_to_user": str(data.get("response_to_user", data.get("content", raw_text[:200]))),
            "detected_theme": data.get("detected_theme"),
            "data_collected": data.get("data_collected", {}),
            "missing_data": data.get("missing_data", []),
            "escalation_reason": data.get("escalation_reason"),
            "estimated_time": data.get("estimated_time")
        })
    except Exception as e:
        logger.warning(f"⚠️ JSON-парсинг не удался ({e}). Пробуем текстовую логику.")

    # 2. Текстовая логика с префиксами
    text = raw_text.strip()
    if text.startswith("[ESCALATE]"):
        reason = text.split(":", 1)[-1].strip() if ":" in text else "эскалация по префиксу"
        return AgentResponse(
            action="escalate",
            response_to_user=text.replace("[ESCALATE]", "", 1).strip(),
            escalation_reason=reason,
            estimated_time="12 часов"
        )
    elif text.startswith("[COLLECT]"):
        return AgentResponse(
            action="collect_data",
            response_to_user=text.replace("[COLLECT]", "", 1).strip(),
            missing_data=["документы"],
            estimated_time="2 часа"
        )
    else:
        return AgentResponse(
            action="reply",
            response_to_user=text,
            estimated_time=""
        )


# === 6. Построение промпта (без строгого JSON-формата) ===
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
    return f"""[КОНТЕКСТ]
USER_ID: {user_id}
Тема: {theme}
Требуемые данные: {rules['required_data']}
Триггеры эскалации: {rules['escalation_conditions']}

[ИНСТРУКЦИЯ]
— Отвечай только на русском.
— Если нужна эскалация — начни с: [ESCALATE] Причина: ...
— Если нужны документы — начни с: [COLLECT] Пожалуйста, пришлите: ...
— Иначе — просто текст: [REPLY] Текст...
— НЕ используй markdown, JSON-обёртки, пояснения.

[ИСТОРИЯ]
{history_preview}

[НОВОЕ СООБЩЕНИЕ]
{user_message}"""


# === 7. Вызов модели с полным логированием ===
async def _call_gemini_with_contents(contents: List[Any]) -> Optional[AgentResponse]:
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
        return parse_gemini_response(response.text)

    except Exception as e:
        logger.exception("💥 Ошибка вызова Gemini")
        return None


# === 8. Fallback-логика на случай падения ИИ ===
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


# === 9. ОСНОВНОЙ ВХОД (асинхронный) ===
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

    # 🔑 Формируем контент: [image?, prompt]
    contents = [user_message]
    if ENABLE_MEDIA_ANALYSIS and image_bytes:
        try:
            mime_type = determine_mime_type(image_bytes, filename)
            logger.info(f"🖼️ Медиа: {len(image_bytes)} байт, MIME={mime_type}")
            image_part = genai.Part.from_data(data=image_bytes, mime_type=mime_type)
            prompt = _build_prompt(user_message, history, theme, user_id, has_media=True)
            contents = [image_part, prompt]
        except Exception as e:
            logger.error(f"❌ Ошибка создания Part: {e}")
            prompt = _build_prompt(user_message, history, theme, user_id, has_media=False)
            contents = [prompt]
    else:
        prompt = _build_prompt(user_message, history, theme, user_id, has_media=False)
        contents = [prompt]

    # Вызов ИИ
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