# services/ai_agent.py
import os
import json
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ValidationError
import asyncio

# === Опциональные импорты Google AI (не падаем, если не установлено) ===
try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False
    logging.warning("google-generativeai не установлен. Используется fallback.")

logger = logging.getLogger(__name__)

# === 1. Единая схема ответа (Pydantic 2.11+) ===
class AgentResponse(BaseModel):
    action: str = Field(..., pattern=r"^(reply|collect_data|escalate)$")
    response_to_user: str
    detected_theme: Optional[str] = None
    data_collected: Dict[str, Any] = Field(default_factory=dict)
    missing_data: List[str] = Field(default_factory=list)
    escalation_reason: Optional[str] = None
    estimated_time: Optional[str] = None

# === 2. Тематические правила (расширяемо) ===
THEME_RULES = {
    "deposit": {
        "required_data": ["bank_screenshot", "bot_receipt", "payment_receipt"],
        "escalation_conditions": ["угроз", "суд", "полиц", "жалоб", "мошенник", "кинули", "обман"]
    },
    "partnership": {
        "required_data": ["partner_type", "traffic", "niche"],
        "escalation_conditions": []
    },
    "default": {
        "required_data": [],
        "escalation_conditions": ["угроз", "суд", "полиц", "жалоб"]
    }
}

# === 3. Кэш модели Gemini ===
_gemini_model = None

def _get_gemini_model() -> Optional["genai.GenerativeModel"]:
    """Ленивая инициализация gemini-2.0-flash"""
    global _gemini_model
    if not _GOOGLE_AVAILABLE:
        return None

    if _gemini_model is None:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY не задан")
            return None

        try:
            genai.configure(api_key=api_key)
            _gemini_model = genai.GenerativeModel(
                model_name="gemini-2.0-flash",  # ← ТОЧНО КАК ЗАПРОШЕНО
                generation_config=GenerationConfig(
                    temperature=0.1,
                    top_p=0.95,
                    max_output_tokens=768,
                    response_mime_type="application/json"
                ),
                system_instruction=(
                    "Ты — ИИ-агент поддержки. Отвечай ТОЛЬКО валидным JSON по схеме AgentResponse."
                )
            )
            # Проверка
            _gemini_model.generate_content(
                "OK", 
                generation_config={"max_output_tokens": 1}
            )
            logger.info("✅ gemini-2.0-flash инициализирована")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации gemini-2.0-flash: {e}")
            return None
    return _gemini_model

# === 4. Построение промпта ===
def _build_prompt(
    user_message: str,
    history: List[Dict[str, Any]],
    theme: str,
    user_id: int
) -> str:
    rules = THEME_RULES.get(theme, THEME_RULES["default"])
    history_preview = "\n".join([
        f"{'👤' if h.get('from_user') else '🤖'}: {h.get('text', '')}"
        for h in history[-5:]
    ])

    return f"""[ЗАДАЧА]
Ты — ИИ-агент поддержки. Помоги пользователю с ID={user_id}.
Тема: {theme}
Требуемые данные: {rules['required_data']}
Триггеры эскалации: {rules['escalation_conditions']}

[ИНСТРУКЦИЯ]
1. Если в сообщении есть триггеры эскалации → action="escalate"
2. Если не хватает данных из required_data → action="collect_data"
3. Иначе → action="reply"
4. estimated_time: "2 часа" для deposit, "1 час" для partnership, "12 часов" иначе

[ФОРМАТ ОТВЕТА СТРОГО JSON]
{{
  "action": "reply|collect_data|escalate",
  "response_to_user": "...",
  "detected_theme": "...",
  "data_collected": {{...}},
  "missing_data": [...],
  "escalation_reason": "...",
  "estimated_time": "..."
}}

[ИСТОРИЯ]
{history_preview}

[СООБЩЕНИЕ]
{user_message}"""

# === 5. Вызов модели с обработкой ошибок ===
async def _call_gemini(prompt: str) -> Optional[AgentResponse]:
    model = _get_gemini_model()
    if not model:
        return None

    try:
        # Асинхронный вызов
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: model.generate_content([prompt])
        )

        if not response or not response.text:
            logger.warning("Gemini: пустой ответ")
            return None

        # Логируем сырой ответ
        logger.debug(f"📥 Gemini raw: {response.text[:200]}...")

        # Валидация через Pydantic
        parsed = json.loads(response.text.strip())
        return AgentResponse.model_validate(parsed)

    except json.JSONDecodeError as e:
        logger.error(f"❌ Невалидный JSON от Gemini: {e} | Ответ: {response.text if 'response' in locals() else 'N/A'}")
    except ValidationError as e:
        logger.error(f"❌ Pydantic validation failed: {e}")
    except Exception as e:
        logger.exception(f"❌ Ошибка вызова Gemini: {e}")

    return None

# === 6. Fallback-логика ===
def _fallback_response(
    user_message: str,
    theme: str,
    history: List[Dict[str, Any]]
) -> AgentResponse:
    text = (user_message or "").lower()
    rules = THEME_RULES.get(theme, THEME_RULES["default"])

    # Проверка триггеров эскалации
    for trigger in rules["escalation_conditions"]:
        if trigger in text:
            return AgentResponse(
                action="escalate",
                response_to_user="Ваш запрос передан оператору.",
                detected_theme=theme,
                escalation_reason=f"Триггер: {trigger}",
                estimated_time="12 часов" if theme != "deposit" else "2 часа"
            )

    # Проверка сбора данных
    has_media = any(msg.get("has_media") for msg in history if msg.get("from_user"))
    if theme == "deposit" and not has_media:
        return AgentResponse(
            action="collect_data",
            response_to_user="Пожалуйста, пришлите скриншоты:\n1. Счёт из банка\n2. Реквизиты из бота\n3. Чек об оплате",
            detected_theme=theme,
            missing_data=["Скрин счёта", "Скрин реквизитов", "Чек"],
            estimated_time="2 часа"
        )

    return AgentResponse(
        action="reply",
        response_to_user="Спасибо за информацию! Оператор свяжется при необходимости.",
        detected_theme=theme,
        estimated_time=""
    )

# === 7. ОСНОВНОЙ ВХОД: backward-совместимый интерфейс ===
def process_ticket(
    *,
    user_message: str,
    history: List[Dict[str, Any]],
    current_theme: Optional[str] = None,
    user_id: int
) -> Dict[str, Any]:
    """
    Backward-compatible entrypoint for handlers/user.py.
    Все параметры — keyword-only.
    Возвращает dict (не AgentResponse), как ожидает старый код.
    """
    theme = current_theme or "default"
    
    # Построение промпта
    prompt = _build_prompt(user_message, history, theme, user_id)
    logger.debug(f"📤 Промпт (длина: {len(prompt)}): {prompt[:150]}...")

    # Попытка вызова Gemini
    try:
        # Превращаем синхронный вызов в async и запускаем
        import asyncio
        loop = asyncio.new_event_loop()
        ai_result = loop.run_until_complete(_call_gemini(prompt))
        loop.close()

        if ai_result:
            logger.info(f"✅ Gemini OK: action={ai_result.action}, theme={ai_result.detected_theme}")
            return ai_result.model_dump()
    except Exception as e:
        logger.exception("Ошибка при вызове Gemini")

    # Fallback
    fallback = _fallback_response(user_message, theme, history)
    logger.warning("⚠️ Используется fallback-логика")
    return fallback.model_dump()