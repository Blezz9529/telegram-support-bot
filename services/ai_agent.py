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

# Настройка логгера
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Кэш модели
_gemini_model = None

# 🔑 ЧЕК-ЛИСТЫ ПО ТЕМАМ
DATA_REQUIREMENTS = {
    "deposit": [
        {"key": "bank_screenshot", "desc": "Скрин счёта из банка", "required": True},
        {"key": "bot_receipt", "desc": "Скрин реквизитов из бота", "required": True},
        {"key": "payment_receipt", "desc": "Чек об оплате", "required": True},
        {"key": "amount", "desc": "Сумма", "required": True},
        {"key": "date", "desc": "Дата операции", "required": True}
    ],
    "partnership": [
        {"key": "partner_type", "desc": "Тип партнёрки (CPL/CPA/CPS)", "required": True},
        {"key": "traffic", "desc": "Охват/трафик", "required": True},
        {"key": "niche", "desc": "Ниша", "required": True}
    ],
    "how_to_play": [
        {"key": "error_details", "desc": "Описание ошибки", "required": False}
    ]
}

# 🔑 ТРИГГЕРЫ ДЛЯ ЭСКАЛАЦИИ
ESCALATION_TRIGGERS = [
    "угроз", "суд", "полиц", "жалоб", "регулятор", "мошенник", "кинули", "обман",
    "возмущ", "требу", "немедленно", "руководств", "админ", "владелец",
    "возврат", "компенсац", "процент", "гаранти"
]


def _get_gemini_model() -> Optional["genai.GenerativeModel"]:
    """Инициализация модели gemini-2.0-flash (точно по названию)"""
    global _gemini_model
    if _gemini_model is not None:
        return _gemini_model

    if not _GOOGLE_AVAILABLE:
        return None

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY не задан — Gemini отключён")
        return None

    try:
        genai.configure(api_key=api_key)
        # ✅ ТОЧНО "gemini-2.0-flash" — как в ТЗ
        _gemini_model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",  # ← СТРОГО КАК ЗАПРОШЕНО
            generation_config=GenerationConfig(
                temperature=0.1,
                top_p=0.95,
                top_k=40,
                max_output_tokens=768,
                response_mime_type="application/json"
            ),
            system_instruction=(
                "Ты — ИИ-агент техподдержки. Управляй заявкой до завершения.\n"
                "ИНСТРУКЦИЯ:\n"
                "1. Анализируй ВСЮ историю диалога.\n"
                "2. Сравнивай с чек-листом для темы.\n"
                "3. Применяй триггеры эскалации.\n"
                "4. Возвращай ТОЛЬКО JSON по схеме.\n\n"
                
                "СХЕМА:\n"
                "{\n"
                '  "action": "reply" | "collect_data" | "escalate",\n'
                '  "response_to_user": "Текст для пользователя",\n'
                '  "detected_theme": "тема или null",\n'
                '  "data_collected": {"ключ": true/false/значение},\n'
                '  "missing_data": ["Описание1", "Описание2"],\n'
                '  "escalation_reason": "Причина или null",\n'
                '  "estimated_time": "12 часов" | "2 часа" | "1 час"\n'
                "}\n\n"
                
                "ЧЕК-ЛИСТЫ:\n"
                "- deposit: скрин счёта, скрин реквизитов, чек, сумма, дата\n"
                "- partnership: тип, охват, ниша\n\n"
                
                "ТРИГГЕРЫ ЭСКАЛАЦИИ:\n"
                "- Угрозы, суд, мошенничество\n"
                "- Для deposit: сумма > 100000 руб, нет скринов после 2 запросов\n"
                "- Партнёрка → всегда collect_data, затем escalate\n\n"
                
                "ВАЖНО:\n"
                "- Не выдумывай данные. Если скрин не прислан — bank_screenshot=false.\n"
                "- estimated_time: deposit=2ч, partnership=1ч, остальное=12ч при escalate/collect_data."
            )
        )
        # Проверка доступности
        _gemini_model.generate_content("test", generation_config={"max_output_tokens": 1})
        logger.info("✅ Модель gemini-2.0-flash инициализирована")
        return _gemini_model
    except Exception as e:
        logger.error(f"Ошибка инициализации gemini-2.0-flash: {e}")
        return None


async def _call_gemini(prompt: str) -> Optional[Dict[str, Any]]:
    """Вызов модели с ПОЛНЫМ логированием промпта"""
    model = _get_gemini_model()
    if not model:
        return None

    # ✅ ПОЛНОЕ ЛОГИРОВАНИЕ ПРОМПТА В ТРЕБУЕМОМ ФОРМАТЕ
    prompt_length = len(prompt)
    # Обрезаем до 500 символов для лога (чтобы не засорять консоль)
    prompt_preview = prompt[:500].replace("\n", "\\n")
    if len(prompt) > 500:
        prompt_preview += "..."
    logger.info(f'📤 Отправка промпта в gemini-2.0-flash (длина: {prompt_length} символов): "{prompt_preview}"')

    try:
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: model.generate_content([prompt])),
            timeout=30.0
        )

        if not response or not response.text:
            logger.warning("gemini-2.0-flash: пустой ответ")
            return None

        # Логируем ответ
        response_preview = response.text[:200].replace("\n", "\\n")
        if len(response.text) > 200:
            response_preview += "..."
        logger.info(f'📥 Получен ответ от gemini-2.0-flash (длина: {len(response.text)}): "{response_preview}"')

        parsed = json.loads(response.text.strip())
        required = {"action", "response_to_user", "data_collected", "missing_data", "escalation_reason", "estimated_time"}
        if not required.issubset(parsed.keys()):
            logger.error(f"gemini-2.0-flash: неполный JSON. Получено: {parsed}")
            return None
        return parsed

    except json.JSONDecodeError as e:
        logger.error(f"gemini-2.0-flash: невалидный JSON: {e} | Ответ: {getattr(response, 'text', 'N/A')}")
        return None
    except asyncio.TimeoutError:
        logger.error("gemini-2.0-flash: таймаут 30с")
        return None
    except gapi_exceptions.ResourceExhausted:
        logger.warning("gemini-2.0-flash: лимит исчерпан")
        return None
    except Exception as e:
        logger.exception(f"gemini-2.0-flash: ошибка: {e}")
        return None


async def process_ticket(
    user_message: str,
    history: List[Dict[str, Any]],
    current_theme: Optional[str],
    user_id: int
) -> Dict[str, Any]:
    """
    Единая точка обработки заявки.
    Принимает историю в формате:
    [
      {"from_user": True, "text": "...", "has_media": False},
      {"from_user": False, "text": "...", "has_media": False}
    ]
    """
    # ЛОГИРУЕМ ВСЁ
    logger.info(f"\n{'='*60}")
    logger.info(f"🆕 НОВОЕ СООБЩЕНИЕ от user_id={user_id}")
    logger.info(f"Тема: {current_theme}")
    logger.info(f"История ({len(history)} сообщений):")
    for i, msg in enumerate(history[-5:], 1):
        role = "👤 Пользователь" if msg.get("from_user") else "🤖 Бот"
        media = " [Медиа]" if msg.get("has_media") else ""
        logger.info(f"  {i}. {role}: {msg.get('text', '')[:100]}{media}")
    logger.info(f"💬 Новое сообщение: {user_message[:200]}...")
    logger.info(f"{'='*60}\n")

    # Формируем историю для промпта
    history_preview = []
    for msg in history[-5:]:
        role = "Пользователь" if msg.get("from_user") else "Бот"
        text = msg.get("text", "") or "[Медиа]"
        history_preview.append(f"{role}: {text}")

    prompt = f"""[КОНТЕКСТ]
USER_ID: {user_id}
Текущая тема: {current_theme or 'не определена'}
Чек-лист для темы: {DATA_REQUIREMENTS.get(current_theme, [])}

[ИСТОРИЯ]
{chr(10).join(history_preview)}

[НОВОЕ СООБЩЕНИЕ]
{user_message}

[ЗАДАЧА]
1. Если тема не определена — определи её.
2. Проанализируй историю: какие данные собраны?
3. Сравни с чек-листом.
4. Проверь триггеры эскалации: {ESCALATION_TRIGGERS}
5. Верни ТОЛЬКО JSON по схеме."""

    result = await _call_gemini(prompt)
    if result:
        logger.info(f"✅ УСПЕШНЫЙ ОТВЕТ: {result}")
        return result

    # === FALLBACK (гарантированно работает) ===
    text = (user_message or "").lower()
    
    if any(trigger in text for trigger in ESCALATION_TRIGGERS):
        return {
            "action": "escalate",
            "response_to_user": "Ваш запрос требует особого внимания — передан оператору.",
            "detected_theme": current_theme,
            "data_collected": {},
            "missing_data": [],
            "escalation_reason": "Обнаружены триггеры эскалации",
            "estimated_time": "12 часов"
        }

    if current_theme == "deposit":
        has_screenshots = "скрин" in text or "screenshot" in text or any(msg.get("has_media") for msg in history[-2:] if msg.get("from_user"))
        has_amount = any(x in text for x in ["руб", "rur", "usd", "сумм", "₽"])
        
        if not has_screenshots:
            return {
                "action": "collect_data",
                "response_to_user": "Чтобы проверить платёж, пришлите:\n1. Скрин счёта из банка\n2. Скрин реквизитов из бота\n3. Чек об оплате",
                "detected_theme": "deposit",
                "data_collected": {"has_screenshots": has_screenshots, "has_amount": has_amount},
                "missing_data": ["Скрин счёта", "Скрин реквизитов", "Чек об оплате"],
                "escalation_reason": None,
                "estimated_time": "2 часа"
            }
        elif not has_amount:
            return {
                "action": "collect_data",
                "response_to_user": "Укажите, пожалуйста, сумму и дату операции.",
                "detected_theme": "deposit",
                "data_collected": {"has_screenshots": has_screenshots, "has_amount": has_amount},
                "missing_data": ["Сумма", "Дата"],
                "escalation_reason": None,
                "estimated_time": "2 часа"
            }

    return {
        "action": "reply",
        "response_to_user": "Спасибо за информацию! Оператор свяжется при необходимости.",
        "detected_theme": current_theme,
        "data_collected": {},
        "missing_data": [],
        "escalation_reason": None,
        "estimated_time": ""
    }