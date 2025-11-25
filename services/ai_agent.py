# services/ai_agent.py
import os
import json
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ValidationError
import asyncio

try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False
    logging.warning("google-generativeai не установлен. Используется fallback.")

logger = logging.getLogger(__name__)

# --- Pydantic модель ---
class AgentResponse(BaseModel):
    action: str = Field(..., pattern=r"^(reply|collect_data|escalate)$")
    response_to_user: str
    detected_theme: Optional[str] = None
    data_collected: Dict[str, Any] = Field(default_factory=dict)
    missing_data: List[str] = Field(default_factory=list)
    escalation_reason: Optional[str] = None
    estimated_time: Optional[str] = None

THEME_RULES = {
    "deposit": {
        "required_data": ["bank_screenshot", "bot_receipt", "payment_receipt"],
        "escalation_conditions": ["угроз", "суд", "полиц", "жалоб", "мошенник"]
    },
    "default": {
        "required_data": [],
        "escalation_conditions": ["угроз", "суд"]
    }
}

_gemini_model = None

def _get_gemini_model():
    global _gemini_model
    if not _GOOGLE_AVAILABLE:
        return None
    if _gemini_model is None:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        try:
            genai.configure(api_key=api_key)
            _gemini_model = genai.GenerativeModel(
                model_name="gemini-2.0-flash",
                generation_config=GenerationConfig(
                    temperature=0.1,
                    top_p=0.95,
                    max_output_tokens=768,
                    response_mime_type="application/json"
                )
            )
            _gemini_model.generate_content("OK", generation_config={"max_output_tokens": 1})
        except Exception as e:
            logger.error(f"Gemini init error: {e}")
            return None
    return _gemini_model

def _build_prompt(user_message: str, history: List[Dict], theme: str, user_id: int) -> str:
    rules = THEME_RULES.get(theme, THEME_RULES["default"])
    history_txt = "\n".join([f"{'👤' if h.get('from_user') else '🤖'}: {h.get('text','')}" for h in history[-5:]])
    return f"""Тема: {theme}, ID: {user_id}
Требуемые данные: {rules['required_data']}
Триггеры: {rules['escalation_conditions']}

История:
{history_txt}

Новое сообщение:
{user_message}

Ответь ТОЛЬКО JSON по схеме:
{{
  "action": "reply|collect_data|escalate",
  "response_to_user": "...",
  "detected_theme": "...",
  "data_collected": {{}},
  "missing_data": [],
  "escalation_reason": "...",
  "estimated_time": "..."
}}"""

async def _call_gemini(prompt: str) -> Optional[AgentResponse]:
    model = _get_gemini_model()
    if not model:
        return None
    try:
        # ✅ НЕТ loop.run_until_complete — просто await
        response = await asyncio.to_thread(
            model.generate_content,
            [prompt]
        )
        if not response.text:
            return None
        parsed = json.loads(response.text.strip())
        return AgentResponse.model_validate(parsed)
    except Exception as e:
        logger.exception("Gemini call failed")
        return None

def _fallback_response(user_message: str, theme: str) -> AgentResponse:
    text = user_message.lower()
    rules = THEME_RULES.get(theme, THEME_RULES["default"])
    for t in rules["escalation_conditions"]:
        if t in text:
            return AgentResponse(
                action="escalate",
                response_to_user="Ваш запрос передан оператору.",
                escalation_reason=f"Триггер: {t}",
                estimated_time="12 часов"
            )
    return AgentResponse(
        action="reply",
        response_to_user="Спасибо за обращение!",
        estimated_time=""
    )

# ✅ ГЛАВНОЕ ИЗМЕНЕНИЕ: async def + без loop
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
    theme = current_theme or "default"
    prompt = _build_prompt(user_message, history, theme, user_id)
    
    # ✅ Прямой await — без run_until_complete
    ai_result = await _call_gemini(prompt)
    if ai_result:
        return ai_result.model_dump()
    
    # Fallback
    fallback = _fallback_response(user_message, theme)
    return fallback.model_dump()