# services/ai_agent.py
import os
import json
import logging
import asyncio
import re
from typing import Any, Dict, List, Optional

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


# === Автоопределение MIME ===
def determine_mime_type( bytes, filename: str = "") -> str:
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
                system_instruction=(
                    "Ты — вежливый и компетентный ИИ-агент поддержки. "
                    "Отвечай кратко, по-русски, только по сути. "
                    "Если нужны документы — попроси конкретно: 'Пожалуйста, пришлите...'. "
                    "Если нужна помощь оператора — скажи: 'Передаю ваш запрос оператору' и вызови эскалацию."
                )
            )
            _gemini_model.generate_content("OK", generation_config={"max_output_tokens": 1})
            logger.info("✅ Gemini: модель gemini-2.0-flash инициализирована")
        except Exception as e:
            logger.error(f"❌ Gemini: ошибка инициализации: {e}")
            return None
    return _gemini_model


# === Очистка текста от мусора ===
def clean_gemini_response(text: str) -> str:
    # Убираем JSON-обёртку вида ["..."] или {"response": "..."}
    try:
        data = json.loads(text.strip())
        if isinstance(data, list) and len(data) > 0:
            text = str(data[0])
        elif isinstance(data, dict) and "response" in data:
            text = str(data["response"])
    except:
        pass

    # Убираем бинарный мусор и непечатаемые символы
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text).strip()

    # Убираем технические префиксы, оставляя только содержание
    text = re.sub(r'^\[(REPLY|COLLECT|ESCALATE)\]\s*', '', text, flags=re.IGNORECASE)

    return text


# === Парсинг действия по ключевым словам ===
def parse_action(text: str) -> Dict[str, Any]:
    lower = text.lower()
    escalation_triggers = ["угроз", "суд", "жалоб", "мошенник", "кинули", "оператор", "человек", "живой"]
    
    if any(t in lower for t in escalation_triggers):
        return {
            "action": "escalate",
            "escalation_reason": "авто-эскалация по ключевым словам"
        }
    return {"action": "reply"}


# === Вызов модели ===
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

        logger.info(f"📥 Gemini: сырой ответ:\n{response.text}")

        # Очистка и парсинг
        clean_text = clean_gemini_response(response.text)
        action_data = parse_action(clean_text)

        logger.info(f"✅ Gemini: очищенный ответ: {clean_text}")

        return {
            "action": action_data["action"],
            "response_to_user": clean_text,
            "escalation_reason": action_data.get("escalation_reason"),
            "estimated_time": "12 часов" if action_data["action"] == "escalate" else ""
        }

    except Exception as e:
        logger.exception("💥 Ошибка вызова Gemini")
        return None


# === Fallback ===
def _fallback_response(user_message: str, theme: str) -> Dict[str, Any]:
    text = (user_message or "").lower()
    if any(t in text for t in ["угроз", "суд", "жалоб", "мошенник"]):
        return {
            "action": "escalate",
            "response_to_user": "Передаю ваш запрос оператору.",
            "escalation_reason": "триггер в сообщении",
            "estimated_time": "12 часов"
        }
    return {
        "action": "reply",
        "response_to_user": "Спасибо за обращение. Оператор скоро свяжется с вами.",
        "estimated_time": ""
    }


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
    theme = current_theme or "deposit"
    logger.info(f"🆕 Запрос ИИ: user_id={user_id}, тема={theme}, сообщение='{user_message}'")

    # Формируем промпт (без JSON-схемы)
    prompt = (
        f"USER_ID: {user_id}\n"
        f"Тема: {theme}\n"
        f"История: {history[-3:]}\n"
        f"Сообщение: {user_message}\n"
        "---\n"
        "Ответь кратко и вежливо на русском. Если нужны документы — попроси конкретно. "
        "Если нужна помощь оператора — скажи: 'Передаю ваш запрос оператору'."
    )

    # Формируем контент
    if ENABLE_MEDIA_ANALYSIS and image_bytes:
        try:
            mime_type = determine_mime_type(image_bytes, filename)
            logger.info(f"🖼️ Медиа: {len(image_bytes)} байт, MIME={mime_type}")
            image_part = {"mime_type": mime_type, "data": image_bytes}
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