# services/ai_agent.py
import os
import logging
import asyncio
import json  # ✅ Импорт в начале — критически важно!
from typing import Dict, Any, List, Optional

# Импорты Google API — в try/except, чтобы не ломать импорт при отсутствии ключа
try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    from google.api_core import exceptions as gapi_exceptions
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False
    logging.warning("Модуль google-generativeai не установлен. Используется fallback-логика.")

from services.localization import load_text, load_ai_message

logger = logging.getLogger(__name__)
_gemini_model = None

def _get_gemini_model() -> Optional["genai.GenerativeModel"]:
    """Ленивая инициализация модели Gemini"""
    global _gemini_model
    if not _GOOGLE_AVAILABLE:
        return None

    if _gemini_model is None:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY не задан — Gemini отключён")
            return None

        try:
            genai.configure(api_key=api_key)
            _gemini_model = genai.GenerativeModel(
                model_name="gemini-1.5-flash",
                generation_config=GenerationConfig(
                    temperature=0.3,
                    top_p=0.95,
                    top_k=40,
                    max_output_tokens=1024,
                    response_mime_type="application/json"
                ),
                system_instruction=(
                    "Ты — ИИ-агент техподдержки. Отвечай строго в JSON. "
                    "need_human=true, если нужен оператор. need_more_info=true, если не хватает данных."
                )
            )
        except Exception as e:
            logger.error(f"Ошибка инициализации Gemini: {e}")
            return None
    return _gemini_model


async def _call_gemini(prompt: str) -> Optional[Dict[str, Any]]:
    """Вызов Gemini без retry (retry реализован в ask_ai)"""
    model = _get_gemini_model()
    if not model:
        return None

    try:
        loop = asyncio.get_event_loop()
        # Таймаут 30 сек
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: model.generate_content([prompt])),
            timeout=30.0
        )

        if not response or not response.text:
            logger.warning("Gemini: пустой ответ")
            return None

        parsed = json.loads(response.text.strip())
        required = {"response_to_user", "need_human", "need_more_info"}
        if not required.issubset(parsed.keys()):
            logger.warning(f"Gemini: неполный JSON: {parsed}")
            return None
        return parsed

    except asyncio.TimeoutError:
        logger.warning("Gemini: таймаут 30с")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Gemini: невалидный JSON: {e} | Ответ: {response.text if 'response' in locals() else 'N/A'}")
        return None
    except Exception as e:
        if _GOOGLE_AVAILABLE:
            from google.api_core import exceptions as gapi_exceptions
            if isinstance(e, gapi_exceptions.ResourceExhausted):
                logger.warning("Gemini: лимит исчерпан")
            elif isinstance(e, gapi_exceptions.FailedPrecondition):
                logger.critical(f"Gemini: ошибка конфигурации (ключ/проект): {e}")
            else:
                logger.exception(f"Gemini: неизвестная ошибка: {e}")
        else:
            logger.debug("Gemini недоступен")
        return None


async def ask_ai(user_message: str, history: List[Dict[str, str]], theme: str, user_id: int) -> Dict[str, Any]:
    """
    Основная точка входа. Всегда возвращает валидный dict.
    """
    prompt = f"""Тема: {theme}
Пользователь ID: {user_id}
Сообщение: {user_message}

Ответь в JSON:
{{
  "response_to_user": "строка",
  "need_human": false,
  "need_more_info": false,
  "additional_questions": "строка",
  "estimated_time": "строка"
}}"""

    # Попытка вызвать Gemini (без retry — чтобы fallback был быстрым)
    try:
        gemini_result = await _call_gemini(prompt)
        if gemini_result:
            logger.info(f"Gemini OK для user_id={user_id}")
            return gemini_result
    except Exception as e:
        logger.exception(f"Ошибка Gemini (fallback): {e}")

    # === FALLBACK: правило-based логика ===
    text = (user_message or "").lower()
    low_words = ["спасибо", "ок", "понял", "ясно", "ладно"]
    high_words = ["ошибка", "не работает", "не зачислили", "заблокировали", "мошенник"]

    if any(w in text for w in low_words):
        return {
            "response_to_user": await load_ai_message("simple_answer_prefix") + " Ваше сообщение получено. Всего доброго!",
            "need_human": False,
            "need_more_info": False,
            "additional_questions": "",
            "estimated_time": ""
        }
    if any(w in text for w in high_words):
        return {
            "response_to_user": await load_ai_message("forwarding_to_manager"),
            "need_human": True,
            "need_more_info": False,
            "additional_questions": "",
            "estimated_time": "10–20 минут"
        }
    return {
        "response_to_user": await load_ai_message("ticket_received", time="5 минут"),
        "need_human": False,
        "need_more_info": True,
        "additional_questions": await load_text("need_more_info"),
        "estimated_time": "5 минут"
    }