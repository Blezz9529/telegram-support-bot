# services/ai_agent.py
import os
import logging
import time
import asyncio
import json  # ✅ ПЕРЕНЕСЛИ сюда — ГЛОБАЛЬНО
from typing import Dict, Any, List, Optional
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from google.api_core import exceptions as gapi_exceptions

from services.localization import load_text, load_ai_message

logger = logging.getLogger(__name__)
_gemini_model = None

# ... остальное без изменений до _call_gemini_with_retry ...

async def _call_gemini_with_retry(
    prompt: str,
    max_retries: int = 3,
    base_delay: float = 1.0
) -> Optional[Dict[str, Any]]:
    model = _get_gemini_model()

    for attempt in range(max_retries):
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: model.generate_content(
                    [prompt],
                    request_options={"timeout": 30}
                )
            )

            if not response or not response.text:
                logger.warning("Gemini вернул пустой ответ")
                return None

            # ✅ Теперь `json` доступен — импорт в начале файла
            result = json.loads(response.text.strip())
            
            required_keys = {"response_to_user", "need_human", "need_more_info"}
            if not all(k in result for k in required_keys):
                logger.warning(f"Неполный JSON от Gemini: {result}")
                return None

            return result

        # ✅ Теперь `json.JSONDecodeError` корректно разрешается
        except json.JSONDecodeError as e:
            logger.error(f"Невалидный JSON от Gemini (попытка {attempt+1}): {e} | Ответ: {response.text if 'response' in locals() else 'N/A'}")
        
        except gapi_exceptions.ResourceExhausted as e:
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

# ... остальное без изменений ...