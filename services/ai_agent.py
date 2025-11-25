# services/ai_agent.py
import os
import json
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ValidationError
import asyncio

# === Опциональные импорты Google AI ===
try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False
    logging.warning("❗ google-generativeai не установлен. Используется fallback.")

logger = logging.getLogger(__name__)


# === 1. Схема ответа (Pydantic 2.11+) ===
class AgentResponse(BaseModel):
    action: str = Field(..., pattern=r"^(reply|collect_data|escalate)$")
    response_to_user: str
    detected_theme: Optional[str] = None
    data_collected: Dict[str, Any] = Field(default_factory=dict)
    missing_data: List[str] = Field(default_factory=list)
    escalation_reason: Optional[str] = None
    estimated_time: Optional[str] = None


# === 2. Тематические правила ===
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
        "escalation_conditions": ["угроз", "суд"]
    }
}


# === 3. Кэш модели ===
_gemini_model = None

def _get_gemini_model() -> Optional["genai.GenerativeModel"]:
    global _gemini_model
    if not _GOOGLE_AVAILABLE:
        logger.warning("❌ Gemini: google-generativeai не импортирован")
        return None

    if _gemini_model is None:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.error("❌ GEMINI_API_KEY не задан")
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
                system_instruction="Ты — ИИ-агент поддержки. Отвечай ТОЛЬКО валидным JSON."
            )
            _gemini_model.generate_content("OK", generation_config={"max_output_tokens": 1})
            logger.info("✅ Gemini: модель gemini-2.0-flash инициализирована")
        except Exception as e:
            logger.error(f"❌ Gemini: ошибка инициализации: {e}")
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
    
    return f"""[КОНТЕКСТ]
USER_ID: {user_id}
Тема: {theme}
Требуемые данные: {rules['required_data']}
Триггеры эскалации: {rules['escalation_conditions']}

[ИНСТРУКЦИЯ]
1. Если есть триггеры → action="escalate"
2. Если не хватает данных → action="collect_data"
3. Иначе → action="reply"
4. estimated_time: "2 ч" для deposit, "1 ч" для partnership, "12 ч" иначе

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

[НОВОЕ СООБЩЕНИЕ]
{user_message}"""


# === 5. Вызов модели с ЛОГИРОВАНИЕМ ===
async def _call_gemini(prompt: str) -> Optional[AgentResponse]:
    model = _get_gemini_model()
    if not model:
        return None

    # 🔑 ЛОГИРУЕМ ВХОД
    prompt_preview = prompt[:300].replace("\n", " ").strip()
    logger.info(f"📤 Gemini: промпт (длина={len(prompt)}): '{prompt_preview}...'")

    try:
        # Асинхронный вызов
        response = await asyncio.to_thread(model.generate_content, [prompt])
        if not response or not response.text:
            logger.warning("❌ Gemini: пустой ответ")
            return None

        # 🔑 ЛОГИРУЕМ ВЫХОД
        response_preview = response.text[:200].replace("\n", " ").strip()
        logger.info(f"📥 Gemini: ответ (длина={len(response.text)}): '{response_preview}...'")

        # Валидация
        parsed = json.loads(response.text.strip())
        validated = AgentResponse.model_validate(parsed)
        logger.info(f"✅ Gemini: валидный ответ → action='{validated.action}', theme='{validated.detected_theme}'")
        return validated

    except json.JSONDecodeError as e:
        logger.error(f"❌ Gemini: невалидный JSON: {e} | Ответ: {response.text if 'response' in locals() else 'N/A'}")
    except ValidationError as e:
        logger.error(f"❌ Pydantic: {e}")
    except Exception as e:
        logger.exception("💥 Gemini: неизвестная ошибка")

    return None


# === 6. Fallback-логика ===
def _fallback_response(user_message: str, theme: str) -> AgentResponse:
    text = (user_message or "").lower()
    rules = THEME_RULES.get(theme, THEME_RULES["default"])

    for trigger in rules["escalation_conditions"]:
        if trigger in text:
            return AgentResponse(
                action="escalate",
                response_to_user="Ваш запрос передан оператору.",
                escalation_reason=f"Триггер: {trigger}",
                estimated_time="2 часа" if theme == "deposit" else "12 часов"
            )

    if theme == "deposit":
        return AgentResponse(
            action="collect_data",
            response_to_user="Пожалуйста, пришлите скриншоты:\n1. Счёт из банка\n2. Реквизиты из бота\n3. Чек об оплате",
            missing_data=["Скрин счёта", "Скрин реквизитов", "Чек"],
            estimated_time="2 часа"
        )

    return AgentResponse(
        action="reply",
        response_to_user="Спасибо за информацию! Оператор свяжется при необходимости.",
        estimated_time=""
    )


# === 7. ОСНОВНОЙ ВХОД (асинхронный, с логами) ===
async def process_ticket(
    *,
    user_message: str,
    history: List[Dict[str, Any]],
    current_theme: Optional[str] = None,
    user_id: int
) -> Dict[str, Any]:
    """
    Асинхронная точка входа. Вызывается как:
        ai_result = await process_ticket(user_message=..., history=..., ...)
    """
    logger.info(f"🆕 Запрос ИИ: user_id={user_id}, тема={current_theme or 'default'}, сообщение='{user_message[:50]}...'")
    
    theme = current_theme or "default"
    prompt = _build_prompt(user_message, history, theme, user_id)

    # Вызов ИИ
    ai_result = await _call_gemini(prompt)
    if ai_result:
        return ai_result.model_dump()

    # Fallback
    logger.warning("⚠️ Используется fallback-логика")
    fallback = _fallback_response(user_message, theme)
    return fallback.model_dump()