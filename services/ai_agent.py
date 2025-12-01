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

from config import (
    GEMINI_MODEL_NAME, GEMINI_TEMPERATURE, GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_RETRY_ATTEMPTS, GEMINI_RETRY_DELAY_BASE
)
from services.localization import load_prompts  # ← будет реализовано ниже

logger = logging.getLogger(__name__)
ENABLE_MEDIA_ANALYSIS = True

# === Кэш описаний изображений ===
_image_summaries = {}
_cache_lock = Lock()

# === Загрузка промптов ===
_prompts_cache = None

def _load_prompts() -> Dict[str, Any]:
    global _prompts_cache
    if _prompts_cache is not None:
        return _prompts_cache
    try:
        with open("locales/prompts.json", "r", encoding="utf-8") as f:
            _prompts_cache = json.load(f)
        logger.info("✅ Промпты загружены из locales/prompts.json")
        return _prompts_cache
    except FileNotFoundError:
        logger.critical("❌ Файл locales/prompts.json не найден!")
        raise
    except json.JSONDecodeError as e:
        logger.critical(f"❌ Ошибка в формате prompts.json: {e}")
        raise


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
            prompts = _load_prompts()
            _gemini_model = genai.GenerativeModel(
                model_name=GEMINI_MODEL_NAME,
                generation_config=GenerationConfig(
                    temperature=GEMINI_TEMPERATURE,
                    top_p=0.95,
                    max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
                    response_mime_type="application/json"
                ),
                system_instruction=prompts["gemini_system_instruction"]
            )
            _gemini_model.generate_content("OK", generation_config={"max_output_tokens": 1})
            logger.info(f"✅ Gemini: модель {GEMINI_MODEL_NAME} инициализирована")
        except Exception as e:
            logger.error(f"❌ Gemini: ошибка инициализации: {e}")
            return None
    return _gemini_model


# === Очистка ответа ===
def clean_gemini_response(text: str) -> tuple[str, bool]:
    try:
        data = json.loads(text.strip())
        if isinstance(data, list) and len(data) > 0:
            text = str(data[0])
        elif isinstance(data, dict) and "response" in 
            text = str(data["response"])
    except:
        pass
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text).strip()
    escalation = text.startswith("[OPERATOR]")
    if escalation:
        text = text.replace("[OPERATOR]", "", 1).strip()
    return text, escalation


# === Анализ изображения ===
async def analyze_image_content(image_bytes: bytes, mime_type: str) -> str:
    model = _get_gemini_model()
    if not model:
        return "[Изображение: Gemini недоступен]"

    prompts = _load_prompts()
    prompt = prompts["gemini_image_analysis_prompt"]

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


# === Кэширование описания изображения ===
async def analyze_and_cache_image(
    image_bytes: bytes,
    user_id: int,
    timestamp: str
) -> str:
    cache_key = (user_id, timestamp)
    with _cache_lock:
        if cache_key in _image_summaries:
            logger.info(f"🖼️ Изображение {cache_key} уже проанализировано (из кэша)")
            return _image_summaries[cache_key]

    mime_type = determine_mime_type(image_bytes)
    summary = await analyze_image_content(image_bytes, mime_type)

    with _cache_lock:
        _image_summaries[cache_key] = summary
    logger.info(f"🖼️ Изображение {cache_key} проанализировано и закэшировано")
    return summary


# === Подготовка истории: изображения → summary ===
async def prepare_history_for_prompt(
    original_history: List[Dict[str, Any]],
    user_id: int
) -> List[Dict[str, Any]]:
    prepared = []
    for msg in original_history:
        if msg.get("has_media") and msg.get("from_user"):
            timestamp = msg.get("timestamp", "unknown")
            img_key = (user_id, timestamp)
            summary = _image_summaries.get(img_key, "[Изображение: описание недоступно]")
            prepared.append({
                "from_user": True,
                "text": f"[ИЗОБРАЖЕНИЕ]\n{summary}",
                "has_media": False,
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

    for attempt in range(GEMINI_RETRY_ATTEMPTS):
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
                delay = (GEMINI_RETRY_DELAY_BASE ** attempt) + random.uniform(0, 1)
                logger.warning(f"⏳ {err_str} — повтор через {delay:.1f} с (попытка {attempt + 1}/{GEMINI_RETRY_ATTEMPTS})")
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
    prompts = _load_prompts()
    triggers = prompts["escalation_keywords"]
    if any(t in text for t in triggers):
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

    # 🔑 1. Анализ изображения (если есть)
    if ENABLE_MEDIA_ANALYSIS and image_bytes:
        try:
            timestamp = history[-1]["timestamp"] if history else "unknown"
            image_summary = await analyze_and_cache_image(image_bytes, user_id, timestamp)
        except Exception as e:
            logger.error(f"❌ Ошибка анализа изображения: {e}")
            image_summary = "[Изображение: ошибка анализа]"

    # 🔑 2. Подготовка истории: изображения → summary
    prepared_history = await prepare_history_for_prompt(history, user_id)

    # 🔑 3. Формируем промпт через шаблон
    prompts = _load_prompts()
    prompt_text = prompts["gemini_main_prompt_template"].format(
        user_id=user_id,
        theme=theme,
        history=prepared_history,
        user_message=user_message
    )

    # 🔑 4. Подготавливаем контент
    if ENABLE_MEDIA_ANALYSIS and image_bytes:
        try:
            mime_type = determine_mime_type(image_bytes, filename)
            logger.info(f"🖼️ Медиа: {len(image_bytes)} байт, MIME={mime_type}")
            contents = [
                {"mime_type": mime_type, "data": image_bytes},
                prompt_text
            ]
        except Exception as e:
            logger.error(f"❌ Ошибка подготовки медиа: {e}")
            contents = [prompt_text]
    else:
        contents = [prompt_text]

    # 🔑 5. Вызов ИИ
    ai_result = await _call_gemini_with_contents(contents)
    if ai_result:
        return ai_result

    logger.warning("⚠️ Используется fallback-логика")
    return _fallback_response(user_message, theme)