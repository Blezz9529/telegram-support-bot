# services/ai_agent.py
import os
import json
import logging
import asyncio
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ValidationError
import subprocess
import sys

# === Установка уровня логирования сразу ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# === Проверка и логирование зависимостей при старте ===
def log_installed_packages():
    try:
        result = subprocess.run([sys.executable, '-m', 'pip', 'list'], 
                              capture_output=True, text=True, timeout=5)
        logger.info("📦 Установленные пакеты (первые 2000 символов):\n" + result.stdout[:2000])
    except Exception as e:
        logger.warning(f"⚠️ Не удалось получить список пакетов: {e}")

log_installed_packages()


# === Импорты Google AI (с логированием ошибок) ===
try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    _GOOGLE_AVAILABLE = True
    logger.info("✅ google-generativeai импортирован")
except ImportError as e:
    logger.critical(f"❌ ОШИБКА импорта google.generativeai: {e!r}")
    logger.exception("Детали:")
    _GOOGLE_AVAILABLE = False


# === Импорты для MIME (optional) ===
try:
    import imghdr
    _IMGHDR_AVAILABLE = True
except ImportError:
    _IMGHDR_AVAILABLE = False


# ✅ МОДУЛЬНЫЙ ФЛАГ: включите/отключите анализ медиа здесь
ENABLE_MEDIA_ANALYSIS = True


# === 1. Строгая Pydantic-модель ответа (исправление model_type) ===
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
        "required_data": ["payment_proof"],
        "escalation_conditions": ["угроз", "суд", "полиц", "жалоб", "мошенник", "кинули", "обман"]
    },
    "partnership": {
        "required_data": ["proposal"],
        "escalation_conditions": []
    },
    "default": {
        "required_data": [],
        "escalation_conditions": ["угроз", "суд"]
    }
}


# === 3. Автоопределение MIME ===
def determine_mime_type(data: bytes, filename: str = "") -> str:
    if _IMGHDR_AVAILABLE:
        img_type = imghdr.what(None, data)
        if img_type:
            return f"image/{img_type}"
    ext_map = {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
        'gif': 'image/gif', 'bmp': 'image/bmp', 'pdf': 'application/pdf'
    }
    if filename:
        ext = filename.lower().split('.')[-1]
        return ext_map.get(ext, 'application/octet-stream')
    return 'application/octet-stream'


# === 4. Инициализация модели ===
_gemini_model = None

def _get_gemini_model() -> Optional["genai.GenerativeModel"]:
    global _gemini_model
    if not _GOOGLE_AVAILABLE:
        return None

    if _gemini_model is None:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.error("❌ GEMINI_API_KEY не задан")
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
                ),
                system_instruction=(
                    "Ты — ИИ-агент поддержки. "
                    "ОТВЕЧАЙ СТРОГО ВАЛИДНЫМ JSON НИЧЕГО КРОМЕ ОБЪЕКТА AgentResponse. "
                    "Не добавляй пояснений, ```json```, комментариев."
                )
            )
            _gemini_model.generate_content("OK", generation_config={"max_output_tokens": 1})
            logger.info("✅ Gemini: модель gemini-2.0-flash инициализирована")
        except Exception as e:
            logger.error(f"❌ Gemini: ошибка инициализации: {e}")
            return None
    return _gemini_model


# === 5. Построение промпта ===
def _build_prompt(
    user_message: str,
    history: List[Dict[str, Any]],
    theme: str,
    user_id: int,
    has_media: bool = False
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
2. Если пользователь отправил фото/документ → action="reply" (анализируй содержимое)
3. Если нет данных по required_data → action="collect_data"
4. estimated_time: "2 ч" для deposit, "1 ч" для partnership, "12 ч" иначе

[ФОРМАТ ОТВЕТА СТРОГО JSON — ТОЛЬКО ОБЪЕКТ, НИЧЕГО КРОМЕ]
{{
  "action": "reply|collect_data|escalate",
  "response_to_user": "строка без кавычек — экранируй как \\\"",
  "detected_theme": "тема или null",
  "data_collected": {{}},
  "missing_data": [],
  "escalation_reason": "строка или null",
  "estimated_time": "строка или null"
}}

[ИСТОРИЯ]
{history_preview}

[НОВОЕ СООБЩЕНИЕ]
{user_message}"""


# === 6. Вызов модели с полным логированием и обработкой ошибок ===
async def _call_gemini_with_contents(contents: List[Any]) -> Optional[AgentResponse]:
    model = _get_gemini_model()
    if not model:
        return None

    # ✅ Полное логирование — без обрезки
    logger.info(f"📤 Gemini: промпт (полный):\n{contents}")

    try:
        response = await asyncio.to_thread(model.generate_content, contents)
        if not response or not response.text:
            logger.warning("❌ Gemini: пустой ответ")
            return None

        logger.info(f"📥 Gemini: ответ (полный):\n{response.text}")

        # ✅ Валидация через Pydantic — устраняет model_type
        return AgentResponse.model_validate(json.loads(response.text.strip()))

    except (json.JSONDecodeError, ValidationError) as e:
        logger.error(f"❌ Невалидный JSON от Gemini: {e}")
        logger.warning(f"Сырой ответ: {response.text[:500]}...")

        # 🔑 Fallback: извлекаем response_to_user вручную
        import re
        match = re.search(r'"response_to_user"\s*:\s*"([^"]*)"', response.text)
        if match:
            text = match.group(1).replace('\\"', '"').replace('\\n', '\n')
            logger.info(f"✅ Удалось извлечь response_to_user: {text}")
            return AgentResponse(
                action="reply",
                response_to_user=text or "Спасибо за обращение.",
                detected_theme=None,
                data_collected={},
                missing_data=[],
                escalation_reason=None,
                estimated_time=""
            )

        return AgentResponse(
            action="reply",
            response_to_user="Спасибо за обращение. Оператор скоро свяжется с вами.",
            detected_theme=None,
            data_collected={},
            missing_data=[],
            escalation_reason=None,
            estimated_time=""
        )

    except Exception as e:
        logger.exception("💥 Ошибка вызова Gemini")
        return None


# === 7. Fallback-логика ===
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
    return AgentResponse(
        action="reply",
        response_to_user="Спасибо за информацию! Оператор свяжется при необходимости.",
        estimated_time=""
    )


# === 8. ОСНОВНОЙ ВХОД (асинхронный) ===
async def process_ticket(
    *,
    user_message: str,
    history: List[Dict[str, Any]],
    current_theme: Optional[str] = None,
    user_id: int,
    image_bytes: Optional[bytes] = None,
    filename: str = ""
) -> Dict[str, Any]:
    theme = current_theme or "default"
    logger.info(f"🆕 Запрос ИИ: user_id={user_id}, тема={theme}, сообщение='{user_message}'")

    # 🔑 Формируем контент
    contents = [user_message]
    if ENABLE_MEDIA_ANALYSIS and image_bytes:
        try:
            mime_type = determine_mime_type(image_bytes, filename)
            logger.info(f"🖼️ Медиа: {len(image_bytes)} байт, MIME={mime_type}")
            image_part = genai.Part.from_data(data=image_bytes, mime_type=mime_type)
            contents = [image_part, user_message]
        except Exception as e:
            logger.error(f"❌ Ошибка создания Part: {e}")

    # Вызов ИИ
    ai_result = await _call_gemini_with_contents(contents)
    if ai_result:
        return ai_result.model_dump()

    # Fallback
    logger.warning("⚠️ Используется fallback-логика")
    fallback = _fallback_response(user_message, theme)
    return fallback.model_dump()