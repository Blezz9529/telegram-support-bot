# services/ai_agent.py
import os
import json
import logging
import asyncio
from typing import Any, Dict, List, Optional

# === Импорты Google AI (без Part) ===
try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    _GOOGLE_AVAILABLE = True
except ImportError as e:
    _GOOGLE_AVAILABLE = False
    logging.critical(f"❌ google-generativeai не установлен: {e!r}")

try:
    import imghdr
    _IMGHDR_AVAILABLE = True
except ImportError:
    _IMGHDR_AVAILABLE = False

logger = logging.getLogger(__name__)
ENABLE_MEDIA_ANALYSIS = True


# === Автоопределение MIME-типа ===
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
        api_key = os.getenv("GEMINI_API_KEY")
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
                system_instruction="Ты — ИИ-агент поддержки. Отвечай ТОЛЬКО в формате: [REPLY|COLLECT|ESCALATE] Текст..."
            )
            _gemini_model.generate_content("OK", generation_config={"max_output_tokens": 1})
            logger.info("✅ Gemini: модель gemini-2.0-flash инициализирована")
        except Exception as e:
            logger.error(f"❌ Gemini: ошибка инициализации: {e}")
            return None
    return _gemini_model


# === Парсинг ответа по префиксам ===
def parse_gemini_response(raw_text: str) -> Dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("[ESCALATE]"):
        reason = text.split(":", 1)[-1].strip() if ":" in text else "эскалация по префиксу"
        return {
            "action": "escalate",
            "response_to_user": text.replace("[ESCALATE]", "", 1).strip(),
            "escalation_reason": reason,
            "estimated_time": "12 часов"
        }
    elif text.startswith("[COLLECT]"):
        return {
            "action": "collect_data",
            "response_to_user": text.replace("[COLLECT]", "", 1).strip(),
            "missing_data": ["документы"],
            "estimated_time": "2 часа"
        }
    else:
        return {
            "action": "reply",
            "response_to_user": text,
            "estimated_time": ""
        }


# === Построение промпта (без JSON-схемы) ===
def _build_prompt(
    user_message: str,
    history: List[Dict[str, Any]],
    theme: str,
    user_id: int,
    has_media: bool = False
) -> str:
    history_preview = "\n".join([
        f"{'👤' if h.get('from_user') else '🤖'}: {h.get('text', '')}"
        for h in history[-5:]
    ])
    return f"""[КОНТЕКСТ]
USER_ID: {user_id}
Тема: {theme}

[ИНСТРУКЦИЯ]
— Ответ должен начинаться с одного из префиксов:
  [REPLY] — обычная информация
  [COLLECT] — нужны документы
  [ESCALATE] — эскалация (угрозы, жалобы, мошенничество)
— После префикса — только текст, никаких JSON, markdown.
— Если есть медиа — проанализируй его и ответь по сути.

[ИСТОРИЯ]
{history_preview}

[НОВОЕ СООБЩЕНИЕ]
{user_message}"""


# === Вызов модели с корректной передачей медиа ===
async def _call_gemini_with_contents(contents: Any) -> Optional[Dict[str, Any]]:
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


# === Fallback ===
def _fallback_response(user_message: str, theme: str) -> Dict[str, Any]:
    text = (user_message or "").lower()
    if any(t in text for t in ["угроз", "суд", "жалоб", "мошенник"]):
        return {
            "action": "escalate",
            "response_to_user": "Ваш запрос передан оператору.",
            "escalation_reason": "триггер в сообщении",
            "estimated_time": "12 часов"
        }
    if theme == "deposit":
        return {
            "action": "collect_data",
            "response_to_user": "Пожалуйста, пришлите любые документы, подтверждающие ваш запрос.",
            "missing_data": ["Любые доказательства"],
            "estimated_time": "2 часа"
        }
    return {
        "action": "reply",
        "response_to_user": "Спасибо за информацию! Оператор свяжется при необходимости.",
        "estimated_time": ""
    }


# === ОСНОВНОЙ ВХОД ===
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

    # Формируем промпт
    prompt = _build_prompt(user_message, history, theme, user_id, bool(image_bytes))

    # Подготавливаем контент
    if ENABLE_MEDIA_ANALYSIS and image_bytes:
        try:
            mime_type = determine_mime_type(image_bytes, filename)
            logger.info(f"🖼️ Медиа: {len(image_bytes)} байт, MIME={mime_type}")
            # ✅ Правильный способ для gemini-2.0-flash (без Part!)
            image_part = {
                "mime_type": mime_type,
                "data": image_bytes
            }
            contents = [image_part, prompt]
        except Exception as e:
            logger.error(f"❌ Ошибка подготовки медиа: {e}")
            contents = [prompt]
    else:
        contents = [prompt]

    # Вызов ИИ
    ai_result = await _call_gemini_with_contents(contents)
    if ai_result:
        return ai_result

    # Fallback
    logger.warning("⚠️ Используется fallback-логика")
    return _fallback_response(user_message, theme)