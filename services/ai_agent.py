from typing import Dict, Any, List

# Заглушка ИИ-агента. Здесь можно подключить LLM (OpenAI, Claude, локальную модель и т.д.)
async def ask_ai(user_message: str, history: List[Dict[str, str]], theme: str, user_id: int) -> Dict[str, Any]:
    """
    Возвращает JSON вида:
    {
      "response_to_user": str,
      "need_human": bool,
      "need_more_info": bool,
      "additional_questions": str,
      "estimated_time": str
    }
    """
    # Пример логики: если в сообщении есть "ошибка" или "не работает" — отправляем человеку
    low_priority_keywords = ["спасибо", "понял", "ок", "хорошо", "ладно"]
    high_priority_keywords = ["ошибка", "не работает", "не зачислили", "заблокировали", "мошенник"]

    text_lower = user_message.lower()

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