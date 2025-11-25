# services/ai_agent.py
import os
import json
import logging
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, ValidationError
import asyncio

# === Опциональные импорты Google AI ===
try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    from google.genai import types  # Для Part.from_bytes
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False
    logging.warning("❗ google-generativeai не установлен. Используется fallback.")

# === Опциональные импорты для автоопределения MIME ===
try:
    import imghdr
    _IMGHDR_AVAILABLE = True
except ImportError:
    _IMGHDR_AVAILABLE = False

try:
    import magic  # python-magic
    _MAGIC_AVAILABLE = True
except ImportError:
    _MAGIC_AVAILABLE = False

logger = logging.getLogger(__name__)

# ✅ МОДУЛЬНЫЙ ФЛАГ: включите анализ медиа здесь
ENABLE_MEDIA_ANALYSIS = True


# === 1. Схема ответа ===
class AgentResponse(BaseModel):
    action: str = Field(..., pattern=r"^(reply|collect_data|escalate)$")
    response_to_user: str
    detected_theme: Optional[str] = None
    data_collected: Dict[str, Any] = Field(default_factory=dict)
    missing_data: List[str] = Field(default_factory=list)
    escalation_reason: Optional[str] = None
    estimated_time: Optional[str] = None


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


# === 3. Автоопределение MIME ===
def determine_mime_type(data: bytes, filename: str = "") -> str:
    """
    Определяет MIME-тип по байтам.
    Приоритет: imghdr → python-magic → расширение → fallback
    """
    # 1. imghdr (встроенный, для изображений)
    if _IMGHDR_AVAILABLE:
        img_type = imghdr.what(None, data)
        if img_type:
            return f"image/{img_type}"

    # 2. python-magic (точнее, если установлен)
    if _MAGIC_AVAILABLE:
        try:
            mime = magic.from_buffer(data, mime=True)
            if mime and isinstance(mime, str):
                return mime
        except Exception as e:
            logger.debug(f"magic.from_buffer failed: {e}")

    # 3. По расширению (если есть имя файла)
    if filename:
        ext = filename.lower().split('.')[-1]
        ext_map = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'gif': 'image/gif',
            'bmp': 'image/bmp',
            'pdf': 'application/pdf',
            'txt': 'text/plain',
        }
        return ext_map.get(ext, 'application/octet-stream')

    # 4. Fallback
    return 'application/octet-stream'


# === 4. Кэш модели ===
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


# === 5. Вызов модели с поддержкой медиа ===
async def _call_gemini_with_contents(contents: List[Any]) -> Optional[AgentResponse]:
    model = _get_gemini_model()
    if not model:
        return None

    # Логируем вход без обрезки
    prompt_parts = []
    for part in contents:
        if isinstance(part, str):
            prompt_parts.append(part)
        elif hasattr(part, 'data'):
            prompt_parts.append("[Медиа]")
        else:
            prompt_parts.append(str(part))
    full_prompt = "\n".join(prompt_parts)
    logger.info(f"📤 Gemini: промпт (длина={len(full_prompt)}):\n{full_prompt}")

    try:
        response = await asyncio.to_thread(model.generate_content, contents)
        if not response or not response.text:
            logger.warning("❌ Gemini: пустой ответ")
            return None

        logger.info(f"📥 Gemini: ответ (длина={len(response.text)}):\n{response.text}")
        return AgentResponse.model_validate(json.loads(response.text.strip()))
    except Exception as e:
        logger.exception("💥 Gemini: ошибка вызова")
        return None


# === 6. Fallback-логика ===
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


# === 7. ОСНОВНОЙ ВХОД (асинхронный) ===
async def process_ticket(
    *,
    user_message: str,
    history: List[Dict[str, Any]],
    current_theme: Optional[str] = None,
    user_id: int,
    image_bytes: Optional[bytes] = None,
    filename: str = ""
) -> Dict[str, Any]:
    """
    Асинхронная точка входа.
    Если ENABLE_MEDIA_ANALYSIS=True и image_bytes не None — анализирует изображение/PDF.
    """
    theme = current_theme or "default"
    logger.info(f"🆕 Запрос ИИ: user_id={user_id}, тема={theme}, сообщение='{user_message[:50]}...'")

    # 🔑 ФОРМИРУЕМ КОНТЕНТ ДЛЯ GEMINI
    contents = [user_message]
    if ENABLE_MEDIA_ANALYSIS and image_bytes:
        try:
            mime_type = determine_mime_type(image_bytes, filename)
            logger.info(f"🖼️ Медиа: {len(image_bytes)} байт, MIME={mime_type}")
            
            # Поддерживаемые типы
            if mime_type.startswith("image/") or mime_type == "application/pdf":
                image_part = types.Part.from_bytes(
                    data=image_bytes,
                    mime_type=mime_type
                )
                contents = [image_part, user_message]
            else:
                logger.warning(f"⚠️ Неподдерживаемый MIME: {mime_type} — пропускаем медиа")
        except Exception as e:
            logger.error(f"❌ Ошибка создания Part: {e}")

    # Вызов
    ai_result = await _call_gemini_with_contents(contents)
    if ai_result:
        return ai_result.model_dump()

    # Fallback
    logger.warning("⚠️ Используется fallback-логика")
    return _fallback_response(user_message, theme).model_dump()