# services/ai_agent.py
import os
import logging
import time
import asyncio
from typing import Dict, Any, List, Optional
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from google.api_core import exceptions as gapi_exceptions

from services.localization import load_text, load_ai_message

# Настройка логгера
logger = logging.getLogger(__name__)

# Глобальная переменная для кэширования модели (опционально)
_gemini_model = None

def _get_gemini_model() -> genai.GenerativeModel:
    """Ленивая инициализация модели Gemini (без повторных вызовов configure)"""
    global _gemini_model
    if _gemini_model is None:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY или OPENAI_API_KEY не задан в .env")

        genai.configure(api_key=api_key)
        # Используем gemini-1.5-flash — быстрый, бесплатный, поддерживает 1M токенов
        _gemini_model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config=GenerationConfig(
                temperature=0.3,
                top_p=0.95,
                top_k=40,
                max_output_tokens=1024,
                response_mime_type="application/json"  # ← Гарантирует JSON
            ),
            system_instruction=(
                "Ты — ИИ-агент техподдержки. Твоя задача — помочь пользователю, "
                "структурированно ответив в формате JSON. "
                "Если вопрос можно решить без оператора — дай чёткий ответ. "
                "Если нужна помощь человека — установи need_human=true. "
                "Если не хватает данных — need_more_info=true и задай 1–2 вопроса. "
                "Ответ ДОЛЖЕН быть строго валидным JSON без пояснений."
            )
        )
    return _gemini_model

# Системный промпт для генерации JSON
JSON_SCHEMA = """
{
  "response_to_user": "Текст для отправки пользователю (макс. 200 символов)",
  "need_human": false,
  "need_more_info": false,
  "additional_questions": "Вопросы, если need_more_info=true (макс. 100 символов)",
  "estimated_time": "Оценка времени, например: '5 минут', '1 час'"
}
"""

async def _call_gemini_with_retry(
    prompt: str,
    max_retries: int = 3,
    base_delay: float = 1.0
) -> Optional[Dict[str, Any]]:
    """Вызов Gemini с экспоненциальной задержкой при ошибках"""
    model = _get_gemini_model()

    for attempt in range(max_retries):
        try:
            # Асинхронный вызов через run_in_executor
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: model.generate_content(
                    [prompt],
                    request_options={"timeout": 30}  # 30 сек таймаут
                )
            )

            if not response or not response.text:
                logger.warning("Gemini вернул пустой ответ")
                return None

            # Парсим JSON
            import json
            result = json.loads(response.text.strip())
            
            # Валидация структуры
            required_keys = {"response_to_user", "need_human", "need_more_info"}
            if not all(k in result for k in required_keys):
                logger.warning(f"Неполный JSON от Gemini: {result}")
                return None

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Невалидный JSON от Gemini (попытка {attempt+1}): {e} | Ответ: {response.text if 'response' in locals() else 'N/A'}")
        
        except gapi_exceptions.ResourceExhausted as e:
            # Лимит: квота исчерпана (RPM/TPM)
            delay = base_delay * (2 ** attempt)
            logger.warning(f"Лимит Gemini исчерпан (попытка {attempt+1}/{max_retries}). Ждём {delay:.1f}с. Ошибка: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
            else:
                raise

        except gapi_exceptions.InvalidArgument as e:
            logger.error(f"Неверный запрос к Gemini: {e}")
            return None

        except gapi_exceptions.FailedPrecondition as e:
            # Например: проект не включён в Cloud Console
            logger.critical(f"Ошибка конфигурации Gemini (FailedPrecondition): {e}")
            return None

        except gapi_exceptions.ServiceUnavailable as e:
            delay = base_delay * (2 ** attempt)
            logger.warning(f"Сервис Gemini недоступен (попытка {attempt+1}). Ждём {delay:.1f}с. Ошибка: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
            else:
                raise

        except gapi_exceptions.GoogleAPIError as e:
            logger.error(f"Общая ошибка Google API: {e}")
            return None

        except Exception as e:
            logger.exception(f"Неожиданная ошибка при вызове Gemini: {e}")
            return None

    return None


async def ask_ai(user_message: str, history: List[Dict[str, str]], theme: str, user_id: int) -> Dict[str, Any]:
    """
    Основная функция ИИ-агента:
    - Сначала пытается использовать Gemini
    - При ошибках/лимитах — откатывается на правило-based заглушку
    """
    prompt = f"""
Контекст:
- Тема обращения: {theme}
- ID пользователя: {user_id}
- История сообщений (последние 3): {history[-3:]}

Сообщение пользователя:
{user_message}

Требования:
1. Ответь строго в формате JSON по схеме:
{JSON_SCHEMA}
2. Если вопрос тривиальный (спасибо, ок, понял) — need_human=false, need_more_info=false.
3. Если в сообщении есть слова: "ошибка", "не работает", "не зачислили", "заблокировали", "мошенник" — need_human=true.
4. Если не хватает данных для решения — need_more_info=true и задай 1–2 уточняющих вопроса.
5. estimated_time — только если need_human=true.
6. response_to_user — дружелюбный, краткий, на русском.
"""

    try:
        gemini_result = await _call_gemini_with_retry(prompt)
        if gemini_result:
            logger.info(f"Gemini успешно ответил для user_id={user_id}")
            return gemini_result
        else:
            logger.warning("Gemini вернул None — используем fallback")
    except Exception as e:
        logger.error(f"Критическая ошибка Gemini — fallback: {e}")

    # === Fallback: правило-based логика (как в первом варианте) ===
    text_lower = (user_message or "").lower()
    low_priority_keywords = ["спасибо", "понял", "ок", "хорошо", "ладно", "ясно"]
    high_priority_keywords = ["ошибка", "не работает", "не зачислили", "заблокировали", "мошенник", "кинули"]

    if any(kw in text_lower for kw in low_priority_keywords):
        return {
            "response_to_user": await load_ai_message("simple_answer_prefix") + " Ваше сообщение получено. Всего доброго!",
            "need_human": False,
            "need_more_info": False,
            "additional_questions": "",
            "estimated_time": ""
        }

    if any(kw in text_lower for kw in high_priority_keywords):
        return {
            "response_to_user": await load_ai_message("forwarding_to_manager"),
            "need_human": True,
            "need_more_info": False,
            "additional_questions": "",
            "estimated_time": "10–20 минут"
        }

    # По умолчанию — запрашиваем детали
    return {
        "response_to_user": await load_ai_message("ticket_received", time="5 минут"),
        "need_human": False,
        "need_more_info": True,
        "additional_questions": await load_text("need_more_info"),
        "estimated_time": "5 минут"
    }