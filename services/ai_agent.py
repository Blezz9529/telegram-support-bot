# services/ai_agent.py
import os
import logging
import asyncio
import json
from typing import Dict, Any, List, Optional

try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False
    logging.warning("google-generativeai не установлен")

logger = logging.getLogger(__name__)
_gemini_model_cache = {}

# 🔑 ЧЕК-ЛИСТЫ ПО ТЕМАМ (что обязательно нужно собрать)
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

# 🔑 ТРИГГЕРЫ ДЛЯ ЭСКАЛАЦИИ (общие + по темам)
ESCALATION_TRIGGERS = {
    "global": [
        "угроз", "суд", "полиц", "жалоб", "регулятор", "мошенник", "кинули", "обман",
        "возмущ", "требу", "немедленно", "руководств", "админ", "владелец"
    ],
    "deposit": ["сумма > 100000", "отсутствие скриншотов после 2 запросов"]
}

def _get_gemini_model() -> Optional["genai.GenerativeModel"]:
    if not _GOOGLE_AVAILABLE:
        return None
    if "main" not in _gemini_model_cache:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        try:
            genai.configure(api_key=api_key)
            _gemini_model_cache["main"] = genai.GenerativeModel(
                model_name="gemini-1.5-flash-002",
                generation_config=GenerationConfig(
                    temperature=0.1,  # ↓ для точности
                    top_p=0.95,
                    max_output_tokens=768,
                    response_mime_type="application/json"
                ),
                system_instruction=(
                    "Ты — ИИ-агент техподдержки. Твоя задача — управлять заявкой до завершения.\n"
                    "ИНСТРУКЦИЯ:\n"
                    "1. Определи ТЕКУЩУЮ тему (если не задана).\n"
                    "2. Проверь историю: какие данные уже собраны?\n"
                    "3. Сравни с ЧЕК-ЛИСТОМ для темы.\n"
                    "4. Примени ТРИГГЕРЫ ЭСКАЛАЦИИ.\n"
                    "5. Верни ТОЛЬКО JSON по схеме ниже.\n\n"
                    
                    "СХЕМА ОТВЕТА:\n"
                    "{\n"
                    '  "action": "reply" | "collect_data" | "escalate",\n'
                    '  "response_to_user": "Текст для отправки (без технических пометок)",\n'
                    '  "data_collected": {"bank_screenshot": true, "amount": "5000", ...},\n'
                    '  "missing_data": ["Скрин счёта", "Чек"],\n'
                    '  "escalation_reason": "Причина эскалации или null",\n'
                    '  "estimated_time": "12 часов" | "2 часа" | "1 час"\n'
                    "}\n\n"
                    
                    "ЧЕК-ЛИСТЫ:\n"
                    "- deposit: скрин счёта, скрин реквизитов, чек, сумма, дата\n"
                    "- partnership: тип, охват, ниша\n"
                    "- how_to_play: описание ошибки (не обязательно)\n\n"
                    
                    "ТРИГГЕРЫ ЭСКАЛАЦИИ:\n"
                    "- Угрозы, суд, мошенничество → escalate\n"
                    "- Для deposit: сумма > 100000 руб, нет скринов после 2 запросов → escalate\n"
                    "- Партнёрка → всегда collect_data, затем escalate\n\n"
                    
                    "ВАЖНО:\n"
                    "- Не выдумывай данные. Если пользователь не прислал скрин — bank_screenshot=false.\n"
                    "- estimated_time: deposit=2ч, partnership=1ч, остальное=12ч при escalate/collect_data.\n"
                    "- При action=reply — estimated_time=\"\"."
                )
            )
        except Exception as e:
            logger.error(f"Ошибка инициализации модели: {e}")
            return None
    return _gemini_model_cache["main"]


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
            return None

        result = json.loads(response.text.strip())
        required = {"action", "response_to_user", "data_collected", "missing_data", "escalation_reason", "estimated_time"}
        if not required.issubset(result.keys()):
            logger.warning(f"Неполный JSON: {result}")
            return None
        return result

    except Exception as e:
        logger.exception("Ошибка Gemini")
        return None


async def process_ticket(
    user_message: str,
    history: List[Dict[str, Any]],
    current_theme: Optional[str],
    user_id: int
) -> Dict[str, Any]:
    """
    Единая точка обработки заявки.
    Возвращает структурированный ответ с чёткими инструкциями.
    """
    # Формируем историю для ИИ (макс. 5 последних сообщений)
    history_preview = []
    for msg in history[-5:]:
        role = "Пользователь" if msg["from_user"] else "Бот"
        text = msg["text"] or "[Медиа]"
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
1. Если тема не определена — определи её по сообщению.
2. Проанализируй историю: какие данные уже собраны?
3. Сравни с чек-листом.
4. Проверь триггеры эскалации.
5. Верни JSON по схеме."""

    try:
        result = await _call_gemini(prompt)
        if result:
            logger.info(f"✅ ИИ обработал заявку user_id={user_id}, action={result['action']}")
            return result
    except Exception as e:
        logger.exception("Fallback на базовую логику")

    # === БАЗОВЫЙ FALLBACK (гарантированно работает) ===
    text = (user_message or "").lower()
    escalation_keywords = ESCALATION_TRIGGERS["global"]
    
    if any(kw in text for kw in escalation_keywords):
        return {
            "action": "escalate",
            "response_to_user": "Ваш запрос требует особого внимания — передан оператору.",
            "data_collected": {},
            "missing_data": [],
            "escalation_reason": "Обнаружены триггеры эскалации",
            "estimated_time": "12 часов"
        }

    if current_theme == "deposit":
        # Проверяем наличие ключевых данных
        has_screenshots = "скрин" in text or "screenshot" in text
        has_amount = any(x in text for x in ["руб", "rur", "usd", "сумм"])
        if not (has_screenshots and has_amount):
            return {
                "action": "collect_data",
                "response_to_user": "Чтобы проверить платёж, нам нужны:\n1. Скрин счёта из банка\n2. Скрин реквизитов из бота\n3. Чек об оплате",
                "data_collected": {"has_screenshots": has_screenshots, "has_amount": has_amount},
                "missing_data": ["Скрин счёта", "Чек"] if not has_screenshots else [],
                "escalation_reason": None,
                "estimated_time": "2 часа"
            }

    return {
        "action": "reply",
        "response_to_user": "Спасибо за информацию! Оператор свяжется при необходимости.",
        "data_collected": {},
        "missing_data": [],
        "escalation_reason": None,
        "estimated_time": ""
    }