# services/ai_agent.py
import os
import logging
import asyncio
import json
from typing import Dict, Any, List, Optional

try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    from google.api_core import exceptions as gapi_exceptions
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False
    logging.warning("Модуль google-generativeai не установлен. Fallback активирован.")

from services.localization import load_text, load_ai_message

logger = logging.getLogger(__name__)
_gemini_model = None

def _get_gemini_model() -> Optional["genai.GenerativeModel"]:
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
            model_name = "gemini-1.5-flash-002"
            _gemini_model = genai.GenerativeModel(
                model_name=model_name,
                generation_config=GenerationConfig(
                    temperature=0.2,
                    top_p=0.95,
                    top_k=40,
                    max_output_tokens=1024,
                    response_mime_type="application/json"
                ),
                system_instruction=(
                    "Ты — ИИ-агент техподдержки. "
                    "Генерируй ТОЛЬКО валидный JSON. "
                    "Если ответ требует оператора или доп. инфо — всегда указывай: "
                    '"estimated_time": "12 часов". '
                    "Схема: {"
                    '"response_to_user":"стр",'
                    '"need_human":bool,'
                    '"need_more_info":bool,'
                    '"additional_questions":"стр",'
                    '"estimated_time":"стр"'
                    "}"
                )
            )
            _gemini_model.generate_content("test", generation_config={"max_output_tokens": 1})
            logger.info(f"✅ Инициализирована модель: {model_name}")
        except Exception as e:
            logger.error(f"Ошибка инициализации {model_name}: {e}")
            return None
    return _gemini_model


async def _call_gemini(prompt: str) -> Optional[Dict[str, Any]]:
    model = _get_gemini_model()
    if not model:
        return None

    try:
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: model.generate_content([prompt])),
            timeout=25.0
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
        logger.warning("Gemini: таймаут 25с")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Gemini: невалидный JSON: {e} | Ответ: {getattr(response, 'text', 'N/A')}")
        return None
    except Exception as e:
        if _GOOGLE_AVAILABLE:
            if "429" in str(e):
                logger.warning("Gemini: лимит RPM/TPM исчерпан")
            elif "API_KEY_INVALID" in str(e):
                logger.critical(f"Gemini: неверный API-ключ: {e}")
            else:
                logger.exception(f"Gemini: ошибка: {e}")
        return None


async def ask_ai(user_message: str, history: List[Dict[str, str]], theme: str, user_id: int) -> Dict[str, Any]:
    prompt = f"""ТЕМА: {theme}
USER_ID: {user_id}
СООБЩЕНИЕ: {user_message}

ИНСТРУКЦИЯ:
- Ответ — ТОЛЬКО JSON, без текста до/после.
- Если need_human=true ИЛИ need_more_info=true → ВСЕГДА: "estimated_time": "12 часов".
- Если need_human=false И need_more_info=false → "estimated_time": "".
- response_to_user — кратко, на русском.
"""

    try:
        gemini_result = await _call_gemini(prompt)
        if gemini_result:
            # ✅ Гарантируем estimated_time = "12 часов" при need_human или need_more_info
            if gemini_result.get("need_human") or gemini_result.get("need_more_info"):
                gemini_result["estimated_time"] = "12 часов"
            else:
                gemini_result.setdefault("estimated_time", "")
            logger.info(f"Gemini OK для user_id={user_id}")
            return gemini_result
    except Exception as e:
        logger.exception(f"Gemini error → fallback: {e}")

    # === FALLBACK (гарантированно соответствует ТЗ) ===
    text = (user_message or "").lower()
    low_words = ["спасибо", "ок", "понял", "ясно", "ладно", "отлично"]
    high_words = ["ошибка", "не работает", "не зачислили", "заблокировали", "мошенник", "кинули"]

    if any(w in text for w in low_words):
        # ✅ Простой ответ — без времени
        return {
            "response_to_user": "Спасибо за обращение! Если понадобится помощь — напишите.",
            "need_human": False,
            "need_more_info": False,
            "additional_questions": "",
            "estimated_time": ""
        }

    if any(w in text for w in high_words):
        # ✅ Оператор нужен → 12 часов
        return {
            "response_to_user": "Ваш запрос передан оператору поддержки.",
            "need_human": True,
            "need_more_info": False,
            "additional_questions": "",
            "estimated_time": "12 часов"
        }

    # ✅ Требуется доп. инфо → 12 часов
    return {
        "response_to_user": "Чтобы помочь, нам нужно уточнить детали:",
        "need_human": False,
        "need_more_info": True,
        "additional_questions": "Пожалуйста, опишите проблему подробнее.",
        "estimated_time": "12 часов"
    }