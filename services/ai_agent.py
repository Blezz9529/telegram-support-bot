# services/ai_agent.py
import os
import json
import logging
import asyncio
import random
import re
from typing import Any, Dict, List, Optional
from threading import Lock

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

# === Кэш описаний изображений ===
_image_summaries = {}
_cache_lock = Lock()


# === MIME-определение ===
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
                    "Ты — вежливый ИИ-агент поддержки. "
                    "Отвечай на русском, кратко, по делу. "
                    "Если нужна помощь оператора — начни ответ с ключевого слова [OPERATOR]."
                )
            )
            _gemini_model.generate_content("OK", generation_config={"max_output_tokens": 1})
            logger.info("✅ Gemini: модель gemini-2.0-flash инициализирована")
        except Exception as e:
            logger.error(f"❌ Gemini: ошибка инициализации: {e}")
            return None
    return _gemini_model


# === Очистка ответа — исправленная версия ===
def clean_gemini_response(text: str) -> tuple[str, bool]:
    """
    Возвращает (очищенный_текст, эскалация_нужна)
    """
    # 1. Пытаемся распарсить JSON
    try:
        data = json.loads(text.strip())
        # Если это dict с "response" — используем его
        if isinstance(data, dict) and "response" in data:
            text = str(data["response"])
        # Если это list — берём первый элемент
        elif isinstance(data, list) and len(data) > 0:
            item = data[0]
            if isinstance(item, dict) and "response" in item:
                text = str(item["response"])
            else:
                text = str(item)
    except json.JSONDecodeError:
        pass  # оставляем как есть

    # 2. Убираем бинарный мусор
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text).strip()

    # 3. Проверяем на эскалацию (везде в тексте)
    escalation = "[OPERATOR]" in text
    text = text.replace("[OPERATOR]", "").strip()
    return text, escalation


# === Анализ изображения (с правильным MIME) ===
async def analyze_image_content(image_bytes: bytes, mime_type: str) -> str:
    """
    Возвращает текстовое описание изображения.
    """
    model = _get_gemini_model()
    if not model:
        return "[Изображение: Gemini недоступен]"

    prompt = (
        "Опиши изображение кратко и по делу. Укажи:\n"
        "1. Что на изображении (чек, счёт, реквизиты и т.п.)\n"
        "2. Ключевые данные (сумма, дата, реквизиты, логотип и т.д.)\n"
        "3. Есть ли признаки ошибки/мошенничества.\n"
        "Ответь на русском, в 2-3 предложениях."
    )

    try:
        loop = asyncio.get_event_loop()
        response = await asyncio.to_thread(
            model.generate_content,
            [{"mime_type": mime_type, "data": image_bytes}, prompt]
        )
        summary = (response.text or "[Изображение: не удалось получить описание]").strip()
        logger.info(f"🖼️ Gemini: анализ изображения (длина={len(image_bytes)} байт): '{summary[:100]}...'")
        return summary
    except Exception as e:
        logger.error(f"❌ Ошибка анализа изображения: {e}")
        return "[Изображение: ошибка анализа]"


# === Кэширование описания ===
async def analyze_and_cache_image(
    image_bytes: bytes,
    user_id: int,
    timestamp: str
) -> str:
    """
    Возвращает описание изображения. Кэширует по (user_id, timestamp).
    """
    cache_key = (user_id, timestamp)
    with _cache_lock:
        if cache_key in _image_summaries:
            logger.info(f"🖼️ Изображение {cache_key} уже проанализировано (из кэша)")
            return _image_summaries[cache_key]

    # Определяем MIME до вызова Gemini
    mime_type = determine_mime_type(image_bytes, f"image_{timestamp}.jpg")
    summary = await analyze_image_content(image_bytes, mime_type)

    # Сохраняем в кэш
    with _cache_lock:
        _image_summaries[cache_key] = summary
    logger.info(f"🖼️ Изображение {cache_key} проанализировано и закэшировано")
    return summary


# === Подготовка истории: изображения → summary ===
async def prepare_history_for_prompt(
    original_history: List[Dict[str, Any]],
    user_id: int
) -> List[Dict[str, Any]]:
    """
    Возвращает историю, где:
    - Если сообщение с has_media=True — заменяем text на "[ИЗОБРАЖЕНИЕ]\n{summary}" (если есть)
    """
    prepared = []
    for msg in original_history:
        if msg.get("has_media") and msg.get("from_user"):
            timestamp = msg.get("timestamp", "unknown")
            img_key = (user_id, timestamp)
            summary = _image_summaries.get(img_key, "[Изображение: описание недоступно]")
            prepared.append({
                "from_user": True,
                "text": f"[ИЗОБРАЖЕНИЕ]\n{summary}",
                "has_media": False,  # ← теперь это текст
                "timestamp": timestamp
            })
        else:
            prepared.append(msg)
    return prepared


# === Вызов модели с retry ===
async def _call_gemini_with_contents(contents: Any) -> Optional[Dict[str, Any]]:
    model = _get_gemini_model()
    if not model:
        return None

    logger.info(f"📤 Gemini: промпт (полный):\n{contents}")

    for attempt in range(3):
        try:
            response = await asyncio.to_thread(model.generate_content, contents)
            if not response or not response.text:
                logger.warning("❌ Gemini: пустой ответ")
                return None

            logger.info(f"📥 Gemini: ответ (сырой):\n{response.text}")
            clean_text, needs_escalation = clean_gemini_response(response.text)
            logger.info(f"✅ Gemini: очищенный ответ: {clean_text}, эскалация: {needs_escalation}")

            action = "escalate" if needs_escalation else "reply"
            estimate = "12 часов" if needs_escalation else ""

            return {
                "action": action,
                "response_to_user": clean_text,
                "escalation_reason": "нужна помощь оператора" if needs_escalation else None,
                "estimated_time": estimate
            }

        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "ResourceExhausted" in err_str or "500" in err_str or "gRPC" in err_str:
                delay = (2 ** attempt) + random.uniform(0, 1)
                logger.warning(f"⏳ {err_str} — повтор через {delay:.1f} с (попытка {attempt + 1}/3)")
                await asyncio.sleep(delay)
                continue
            else:
                logger.exception("💥 Другая ошибка Gemini")
                break

    logger.warning("⚠️ Все попытки исчерпаны. Используется fallback с эскалацией")
    return {
        "action": "escalate",
        "response_to_user": "Передаю ваш запрос оператору.",
        "escalation_reason": "ошибка Gemini после 3 попыток",
        "estimated_time": "12 часов"
    }


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
        "response_to_user": "Спасибо за информацию. Оператор скоро свяжется с вами.",
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

    # 🔑 1. Анализ изображения (если есть) → кэшируем
    if ENABLE_MEDIA_ANALYSIS and image_bytes:
        try:
            timestamp = history[-1]["timestamp"] if history else "unknown"
            image_summary = await analyze_and_cache_image(image_bytes, user_id, timestamp)
        except Exception as e:
            logger.error(f"❌ Ошибка анализа изображения: {e}")
            image_summary = "[Изображение: ошибка анализа]"

    # 🔑 2. Подготовка истории: изображения → summary
    prepared_history = await prepare_history_for_prompt(history, user_id)

    prompt = (
        f"USER_ID: {user_id}\n"
        f"Тема: {theme}\n"
        f"История: {prepared_history}\n"
        f"Сообщение: {user_message}\n"
        "---\n"
        "Ответь кратко и вежливо на русском. Если нужны документы — попроси конкретно. "
        "Если нужна помощь оператора — начни ответ с ключевого слова [OPERATOR]."
    )

    # Подготавливаем контент
    if ENABLE_MEDIA_ANALYSIS and image_bytes:
        try:
            mime_type = determine_mime_type(image_bytes, filename)
            logger.info(f"🖼️ Медиа: {len(image_bytes)} байт, MIME={mime_type}")
            contents = [
                {"mime_type": mime_type, "data": image_bytes},
                prompt
            ]
        except Exception as e:
            logger.error(f"❌ Ошибка подготовки медиа: {e}")
            contents = [prompt]
    else:
        contents = [prompt]

    ai_result = await _call_gemini_with_contents(contents)
    if ai_result:
        return ai_result

    logger.warning("⚠️ Используется fallback-логика")
    return _fallback_response(user_message, theme)