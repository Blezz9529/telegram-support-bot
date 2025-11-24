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

# Кэш моделей ПО ТЕМЕ (ускоряет повторные вызовы)
_gemini_model_cache: Dict[str, "genai.GenerativeModel"] = {}


# 🔑 ТЕМАТИЧЕСКИЕ ИНСТРУКЦИИ (с правилами need_human и кодовым словом)
THEME_INSTRUCTIONS = {
    "feedback": (
        "Ты — агент сбора отзывов. "
        "Задача: получить структурированный отзыв. "
        "Правила need_human=true: "
        "1. Упоминание 'угроза', 'суд', 'жалоба в регулятора' "
        "2. Оценка ≤ 2/5 без пояснения "
        "3. Требование 'немедленно связать с руководством'. "
        "Всегда добавляй [[PUSH_OPERATOR]] в response_to_user, если need_human=true. "
        "estimated_time: '12 часов' при need_human или need_more_info."
    ),
    "deposit": (
        "Ты — эксперт по платежам. "
        "Требуй: сумму, дату, способ оплаты, скриншот чека. "
        "Правила need_human=true: "
        "1. Упоминание 'мошенник', 'кинули', 'обман', 'полиция' "
        "2. Сумма > 100000 руб. "
        "3. Отсутствие скриншота при запросе 2+ раз. "
        "Всегда добавляй [[PUSH_OPERATOR]] в response_to_user, если need_human=true. "
        "estimated_time: '2 часа' при need_human, '12 часов' при need_more_info."
    ),
    "how_to_play": (
        "Ты — гид по игре. "
        "Отвечай только по официальной документации. "
        "Правила need_human=true: "
        "1. Вопрос про 'баг', 'ошибка', 'краш', 'не запускается' "
        "2. Упоминание 'потерял аккаунт', 'взломали' "
        "3. Запрос 'возврат средств за внутриигровые покупки'. "
        "Всегда добавляй [[PUSH_OPERATOR]] в response_to_user, если need_human=true. "
        "estimated_time: '12 часов'."
    ),
    "earn": (
        "Ты — консультант по заработку. "
        "Правила need_human=true: "
        "1. Упоминание 'гарантия дохода', 'возврат вложений' "
        "2. Сумма 'инвестиций' > 5000 руб. "
        "3. Фразы 'как начать без вложений', 'гарантированно'. "
        "Всегда добавляй [[PUSH_OPERATOR]] в response_to_user, если need_human=true. "
        "estimated_time: '1 час' при need_human, '12 часов' при need_more_info."
    ),
    "partnership": (
        "Ты — менеджер по партнёркам. "
        "Правила need_human=true: ВСЕГДА (требуется ручное ТЗ). "
        "Дополнительно: "
        "- Запрашивай: тип партнёрки (CPL/CPA/CPS), охват, нишу, гео. "
        "- Если нет данных по 2+ пунктам → need_more_info=true. "
        "Всегда добавляй [[PUSH_OPERATOR]] в response_to_user. "
        "estimated_time: '1 час'."
    ),
    "other": (
        "Ты — ИИ-агент поддержки. "
        "Правила need_human=true: "
        "1. Упоминание 'ошибка', 'не работает', 'заблокировали', 'мошенник' "
        "2. Эмоциональные фразы: 'возмущён', 'требую', 'жалоба' "
        "3. Повторное обращение по одной теме (история > 2 сообщений). "
        "Всегда добавляй [[PUSH_OPERATOR]] в response_to_user, если need_human=true. "
        "estimated_time: '12 часов'."
    )
}


def _get_gemini_model(theme: str = "other") -> Optional["genai.GenerativeModel"]:
    """Ленивая инициализация модели ПО ТЕМЕ"""
    if not _GOOGLE_AVAILABLE:
        return None

    # Нормализуем тему
    theme = theme if theme in THEME_INSTRUCTIONS else "other"

    if theme not in _gemini_model_cache:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY не задан — Gemini отключён")
            return None

        try:
            genai.configure(api_key=api_key)
            system_instr = THEME_INSTRUCTIONS[theme]
            
            _gemini_model_cache[theme] = genai.GenerativeModel(
                model_name="gemini-2.0-flash",
                generation_config=GenerationConfig(
                    temperature=0.2,
                    top_p=0.95,
                    top_k=40,
                    max_output_tokens=512,
                    response_mime_type="application/json"
                ),
                system_instruction=system_instr
            )
            # Проверка
            _gemini_model_cache[theme].generate_content("OK", generation_config={"max_output_tokens": 1})
            logger.info(f"✅ Модель для темы '{theme}' инициализирована")
        except Exception as e:
            logger.error(f"Ошибка инициализации модели '{theme}': {e}")
            return None
    return _gemini_model_cache[theme]


async def _call_gemini(prompt: str, theme: str) -> Optional[Dict[str, Any]]:
    """Вызов Gemini с тематической моделью"""
    model = _get_gemini_model(theme)
    if not model:
        return None

    try:
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: model.generate_content([prompt])),
            timeout=20.0
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
        logger.warning("Gemini: таймаут 20с")
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
    """
    Основная функция ИИ-агента.
    Возвращает dict с полями:
      - response_to_user: str (может содержать [[PUSH_OPERATOR]])
      - need_human: bool
      - need_more_info: bool
      - additional_questions: str
      - estimated_time: str
    """
    # Нормализуем тему для инструкции
    theme_for_instr = theme if theme in THEME_INSTRUCTIONS else "other"
    
    prompt = f"""[КОНТЕКСТ]
Тема: {theme}
USER_ID: {user_id}
История (последние 3): {history[-3:]}

[СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ]
{user_message}

[ТРЕБОВАНИЯ]
1. Ответ — ТОЛЬКО валидный JSON.
2. Если need_human=true — ДОБАВЬ [[PUSH_OPERATOR]] в response_to_user.
3. estimated_time:
   - deposit + need_human → "2 часа"
   - earn/partnership + need_human → "1 час"
   - иначе → "12 часов" (при need_human или need_more_info)
4. Схема:
{{
  "response_to_user": "строка",
  "need_human": булево,
  "need_more_info": булево,
  "additional_questions": "строка",
  "estimated_time": "строка"
}}"""

    try:
        gemini_result = await _call_gemini(prompt, theme_for_instr)
        if gemini_result:
            # Обеспечиваем наличие estimated_time
            if gemini_result.get("need_human") or gemini_result.get("need_more_info"):
                if theme == "deposit" and gemini_result["need_human"]:
                    gemini_result["estimated_time"] = "2 часа"
                elif theme in ["earn", "partnership"] and gemini_result["need_human"]:
                    gemini_result["estimated_time"] = "1 час"
                else:
                    gemini_result["estimated_time"] = "12 часов"
            else:
                gemini_result.setdefault("estimated_time", "")
            logger.info(f"Gemini OK для user_id={user_id}, theme={theme}")
            return gemini_result
    except Exception as e:
        logger.exception(f"Gemini error → fallback: {e}")

    # === FALLBACK (гарантированно совместим с правилами выше) ===
    text = (user_message or "").lower()
    low_words = ["спасибо", "ок", "понял", "ясно", "ладно"]
    high_words = ["ошибка", "не работает", "заблокировали", "мошенник", "кинули", "угроза", "суд"]
    deposit_words = ["деньги", "платеж", "транзакция", "счёт", "баланс"]
    partner_words = ["партнёр", "сотрудничество", "партнёрка", "партнер"]

    need_human = False
    estimated_time = "12 часов"

    # Правила need_human по темам
    if theme == "deposit" and any(w in text for w in ["мошенник", "кинули", "полиция"]):
        need_human = True
        estimated_time = "2 часа"
    elif theme == "partnership":
        need_human = True
        estimated_time = "1 час"
    elif any(w in text for w in high_words):
        need_human = True

    if any(w in text for w in low_words):
        return {
            "response_to_user": "Спасибо за обращение!",
            "need_human": False,
            "need_more_info": False,
            "additional_questions": "",
            "estimated_time": ""
        }

    if need_human:
        push_tag = " [[PUSH_OPERATOR]]" if theme != "partnership" else " [[PUSH_OPERATOR]]"
        return {
            "response_to_user": f"Ваш запрос передан оператору.{push_tag}",
            "need_human": True,
            "need_more_info": False,
            "additional_questions": "",
            "estimated_time": estimated_time
        }

    # Запрос доп. информации
    if theme == "deposit":
        questions = "Укажите: 1) Сумму платежа, 2) Дату, 3) Способ оплаты, 4) Пришлите скриншот чека."
        estimated_time = "12 часов"
    elif theme == "partnership":
        questions = "Опишите: тип партнёрки (CPL/CPA), ваш охват, нишу и гео."
        estimated_time = "1 час"
    else:
        questions = "Пожалуйста, уточните детали вашего вопроса."

    return {
        "response_to_user": "Чтобы помочь, нам нужно больше информации:",
        "need_human": False,
        "need_more_info": True,
        "additional_questions": questions,
        "estimated_time": estimated_time
    }