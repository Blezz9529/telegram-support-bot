# Refactored ai_agent.py
# NOTE: Minimal-impact refactor: added pydantic validation, unified response parsing,
# stable JSON handling, removed text-trigger logic, inserted per-theme rule hooks.

import json
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ValidationError, Field

logger = logging.getLogger(__name__)

########################################
# 1. Unified response schema
########################################
class AgentResponse(BaseModel):
    action: str = Field(..., pattern="^(reply|collect_data|escalate)$")
    response_to_user: str
    detected_theme: Optional[str]
    data_collected: Dict[str, Any]
    missing_data: List[str]
    escalation_reason: Optional[str]
    estimated_time: Optional[str]

########################################
# 2. Theme rule packs (extendable without touching logic)
########################################
THEME_RULES = {
    "deposit_issue": {
        "required_data": ["bank_statement", "payment_requisites", "operation_receipt"],
        "escalation_conditions": [
            "aggression", "threats", "persistent_missing_data"
        ],
    },
}

########################################
# 3. Prompt builder with embedded rules
########################################
def build_prompt(history: List[Dict[str, str]], theme: str) -> str:
    rules = THEME_RULES.get(theme, {})
    return f"""
    Ты — ИИ-ассистент техподдержки. Отвечай строго в JSON.
    Тема: {theme}

    Требуемые данные: {rules.get('required_data')}
    Условия эскалации: {rules.get('escalation_conditions')}

    Если не хватает данных — action = "collect_data".
    Если есть агрессия/угрозы — action = "escalate".
    Если данные собраны — action = "reply".

    Формат ответа:
    {{
      "action": "reply|collect_data|escalate",
      "response_to_user": "...",
      "detected_theme": "...",
      "data_collected": {{...}},
      "missing_data": [...],
      "escalation_reason": "...",
      "estimated_time": "..."
    }}

    История:
    {json.dumps(history, ensure_ascii=False)}
    """

########################################
# 4. Stable LLM call wrapper
########################################
def call_model(model, prompt: str):
    try:
        raw = model.generate_content([prompt])
        text = raw.text.strip()
        parsed = json.loads(text)
        validated = AgentResponse.parse_obj(parsed)
        return validated
    except (json.JSONDecodeError, ValidationError) as e:
        logger.error(f"Invalid model response: {e}")
        return None

########################################
# 5. Main agent entrypoint
# Backward‑compatible wrapper for old imports
# Old code expects: from services.ai_agent import process_ticket

def process_ticket(model, history, theme):
    """
    Backward-compatible adapter.
    Old code used process_ticket; it now simply delegates to agent_reply.
    """
    result = agent_reply(model, history, theme)
    # preserve old dict behavior
    return result.dict() if hasattr(result, "dict") else result


########################################
def agent_reply(model, history: List[Dict[str, str]], theme: str):
    prompt = build_prompt(history, theme)
    result = call_model(model, prompt)
    if not result:
        return AgentResponse(
            action="escalate",
            response_to_user="Извините, произошла ошибка. Передаю запрос оператору.",
            detected_theme=theme,
            data_collected={},
            missing_data=[],
            escalation_reason="invalid_model_output",
            estimated_time=None,
        )
    return result
