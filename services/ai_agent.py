# services/ai_agent.py
import os
import json
import logging
import asyncio
import random
import re
from typing import Any, Dict, List, Optional
from threading import Lock

try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    _GOOGLE_AVAILABLE = True
except ImportError as e:
    _GOOGLE_AVAILABLE = False
    logging.critical(f"❌ google-generativeai не установлен: {e!r}")

try:
    import imghdr
    _IMGHDR_AVAILABLE = True
except ImportError:
    _IMGHDR_AVAILABLE = False

logger = logging.getLogger(__name__)
ENABLE_MEDIA_ANALYSIS = True

# === Кэш описаний изображений ===
_image_summaries = {}
_cache_lock = Lock()


# === Загрузка промптов (с поддержкой любой структуры) ===
def load_prompts() -> Dict[str, str]:
    try:
        with open("locales/prompts.json", "r", encoding="utf-8") as f:
            content = f.read()
            content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', content).strip()
            data = json.loads(content)

            # Build system instruction from CORE_STYLE_RULES if available
            system_instruction = data.get("gemini_system_instruction", "")
            if not system_instruction and "CORE_STYLE_RULES" in data:
                core = data["CORE_STYLE_RULES"]
                strict = data.get("STRICT_BEHAVIOR_RULES", {})
                operator = data.get("OPERATOR_RULES", {})
                persona = core.get("persona", "ИИ-агент поддержки")
                tone = core.get("tone", "кратко, по делу")
                lang = core.get("language", "Русский")
                max_sentences = strict.get("max_sentences", 3)
                operator_prefix = core.get("operator_prefix", "[OPERATOR]")
                system_instruction = (
                    f"Ты — {persona}. Язык: {lang}. Тон: {tone}. "
                    f"Максимум {max_sentences} предложения в ответе. "
                    f"Если нужна помощь оператора — начни ответ с {operator_prefix}."
                )
            elif isinstance(system_instruction, dict):
                persona = system_instruction.get("role_style", {}).get("persona", "ИИ-агент поддержки")
                tone = system_instruction.get("role_style", {}).get("tone", "кратко, по делу")
                operator_rule = system_instruction.get("general_rules", {}).get("operator_call_format", "[OPERATOR]")
                system_instruction = f"Ты — {persona}. {tone}. Если нужна помощь оператора — начни ответ с ключевого слова {operator_rule}."
            elif not system_instruction:
                system_instruction = "Ты — вежливый ИИ-агент поддержки. Отвечай на русском, кратко, по делу. Если нужна помощь оператора — начни ответ с [OPERATOR]."

            # Build image prompt from IMAGE_ANALYSIS if available
            image_prompt = data.get("gemini_image_analysis_prompt", "")
            if not image_prompt and "IMAGE_ANALYSIS" in data:
                img = data["IMAGE_ANALYSIS"]
                reqs = img.get("requirements", [])
                image_prompt = f"Опиши изображение в {img.get('format', '1-2 предложения')}. Укажи: {', '.join(reqs)}."
            elif not image_prompt:
                image_prompt = "Опиши изображение кратко и по делу. Укажи: что на изображении, ключевые данные. Ответь на русском, в 2-3 предложениях."

            # Get main prompt template
            main_prompt = data.get("gemini_main_prompt_template", "") or data.get("MAIN_PROMPT_TEMPLATE", "")
            if not main_prompt:
                main_prompt = "USER_ID: {user_id}\nТема: {theme}\nИстория: {history}\nСообщение: {user_message}\n---\nОтветь кратко и вежливо на русском. Если нужны документы — попроси конкретно. Если нужна помощь оператора — начни ответ с [OPERATOR]."

            return {
                "gemini_system_instruction": system_instruction,
                "gemini_image_analysis_prompt": image_prompt,
                "gemini_main_prompt_template": main_prompt
            }

    except FileNotFoundError:
        logger.warning("⚠️ locales/prompts.json не найден — использую fallback")
    except json.JSONDecodeError as e:
        logger.error(f"❌ Ошибка парсинга prompts.json: {e}")
    except Exception as e:
        logger.exception(f"💥 Ошибка загрузки промптов: {e}")

    # Fallback
    return {
        "gemini_system_instruction": (
            "Ты — вежливый ИИ-агент поддержки. "
            "Отвечай на русском, кратко, по делу. "
            "Если нужна помощь оператора — начни ответ с ключевого слова [OPERATOR]."
        ),
        "gemini_image_analysis_prompt": (
            "Опиши изображение кратко и по делу. Укажи:\n"
            "1. Что на изображении (чек, счёт, реквизиты и т.п.)\n"
            "2. Ключевые данные (сумма, дата, реквизиты, логотип и т.д.)\n"
            "Ответь на русском, в 2-3 предложениях."
        ),
        "gemini_main_prompt_template": (
            "USER_ID: {user_id}\n"
            "Тема: {theme}\n"
            "История: {history}\n"
            "Сообщение: {user_message}\n"
            "---\n"
            "Ответь кратко и вежливо на русском. Если нужны документы — попроси конкретно. "
            "Если нужна помощь оператора — начни ответ с ключевого слова [OPERATOR]."
        )
    }


# === MIME-определение ===
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


# === Инициализация модели ===
_gemini_model = None

def _get_gemini_model() -> Optional["genai.GenerativeModel"]:
    global _gemini_model
    if not _GOOGLE_AVAILABLE:
        return None
    if _gemini_model is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("❌ GEMINI_API_KEY не задан")
            return None
        try:
            genai.configure(api_key=api_key)
            prompts = load_prompts()
            _gemini_model = genai.GenerativeModel(
                model_name="gemini-2.0-flash",
                generation_config=GenerationConfig(
                    temperature=0.1,
                    top_p=0.95,
                    max_output_tokens=768,
                    response_mime_type="application/json"
                ),
                system_instruction=prompts["gemini_system_instruction"]
            )
            logger.info("✅ Gemini: модель gemini-2.0-flash инициализирована")
        except Exception as e:
            logger.error(f"❌ Gemini: ошибка инициализации: {e}")
            return None
    return _gemini_model


# === Очистка ответа (исправлена: удаляет [OPERATOR] из любого места) ===
def clean_gemini_response(text: str) -> tuple[str, bool]:
    try:
        data = json.loads(text.strip())
        if isinstance(data, list) and len(data) > 0:
            text = str(data[0])
        elif isinstance(data, dict) and "response" in data:
            text = str(data["response"])
    except:
        pass
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text).strip()

    # 🔑 УДАЛЯЕМ [OPERATOR] из любого места в тексте (не только в начале)
    escalation = "[OPERATOR]" in text
    text = text.replace("[OPERATOR]", "").strip()
    # Убираем лишние пробелы после удаления
    text = re.sub(r'\s+', ' ', text).strip()

    return text, escalation


# === Анализ изображения и кэширование ===
async def analyze_and_cache_image(
    image_bytes: bytes,
    user_id: int,
    timestamp: str,
    filename: str = ""
) -> str:
    cache_key = (user_id, timestamp)
    with _cache_lock:
        if cache_key in _image_summaries:
            logger.info(f"🖼️ Изображение {cache_key} уже проанализировано (из кэша)")
            return _image_summaries[cache_key]

    model = _get_gemini_model()
    if not model:
        summary = "[Изображение: Gemini недоступен]"
    else:
        prompts = load_prompts()
        prompt = prompts["gemini_image_analysis_prompt"]
        try:
            loop = asyncio.get_event_loop()
            response = await asyncio.to_thread(
                model.generate_content,
                [{"mime_type": "image/jpeg", "data": image_bytes}, prompt]
            )
            summary = (response.text or "[Изображение: не удалось получить описание]").strip()
        except Exception as e:
            logger.error(f"❌ Ошибка анализа изображения: {e}")
            summary = "[Изображение: ошибка анализа]"

    # ✅ ЛОГИРУЕМ РЕЗУЛЬТАТ АНАЛИЗА
    logger.info(f"🖼️ Изображение {cache_key} проанализировано. Результат: {summary}")

    with _cache_lock:
        _image_summaries[cache_key] = summary
    return summary


# === Подготовка истории: изображения → summary ===
async def prepare_history_for_prompt(
    original_history: List[Dict[str, Any]],
    user_id: int
) -> List[Dict[str, Any]]:
    prepared = []
    seen_images = set()  # (user_id, timestamp,)
    for msg in original_history:
        if msg.get("has_media") and msg.get("from_user"):
            timestamp = msg.get("timestamp", "unknown")
            img_key = (user_id, timestamp)
            if img_key in seen_images:
                # Уже видели — заменяем на summary
                summary = _image_summaries.get(img_key, "[Изображение: описание недоступно]")
                prepared.append({
                    "from_user": True,
                    "text": f"[ИЗОБРАЖЕНИЕ]\n{summary}",
                    "has_media": False,
                    "timestamp": timestamp
                })
            else:
                # Первый раз — оставляем как есть (байты передаются отдельно)
                prepared.append(msg)
                seen_images.add(img_key)
        else:
            prepared.append(msg)
    return prepared


# === Вызов модели с retry и логированием ===
async def _call_gemini_with_contents(contents: Any) -> Optional[Dict[str, Any]]:
    model = _get_gemini_model()
    if not model:
        return None

    # ✅ ЛОГИРУЕМ ПОЛНОСТЬЮ ПРОМПТ
    logger.info(f"📤 Gemini: промпт (полный):\n{contents}")

    for attempt in range(3):
        try:
            response = await asyncio.to_thread(model.generate_content, contents)
            if not response or not response.text:
                logger.warning("❌ Gemini: пустой ответ")
                return None

            # ✅ ЛОГИРУЕМ СЫРОЙ ОТВЕТ
            logger.info(f"📥 Gemini: ответ (сырой):\n{response.text}")
            clean_text, needs_escalation = clean_gemini_response(response.text)
            # ✅ ЛОГИРУЕМ ОЧИЩЕННЫЙ ОТВЕТ
            logger.info(f"✅ Gemini: очищенный ответ: {clean_text}, эскалация: {needs_escalation}")

            action = "escalate" if needs_escalation else "reply"
            estimate = "12 часов" if needs_escalation else ""

            return {
                "action": action,
                "response_to_user": clean_text,
                "escalation_reason": "нужна помощь оператора" if needs_escalation else None,
                "estimated_time": estimate
            }

        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "ResourceExhausted" in err_str or "500" in err_str or "gRPC" in err_str:
                delay = (2 ** attempt) + random.uniform(0, 1)
                logger.warning(f"⏳ {err_str} — повтор через {delay:.1f} с (попытка {attempt + 1}/3)")
                await asyncio.sleep(delay)
                continue
            else:
                logger.exception("💥 Другая ошибка Gemini")
                break

    logger.warning("⚠️ Все попытки исчерпаны. Используется fallback с эскалацией")
    return {
        "action": "escalate",
        "response_to_user": "Передаю ваш запрос оператору.",
        "escalation_reason": "ошибка Gemini после 3 попыток",
        "estimated_time": "12 часов"
    }


# === Fallback ===
def _fallback_response(user_message: str, theme: str) -> Dict[str, Any]:
    text = (user_message or "").lower()
    if any(t in text for t in ["угроз", "суд", "жалоб", "мошенник"]):
        return {
            "action": "escalate",
            "response_to_user": "Передаю ваш запрос оператору.",
            "escalation_reason": "триггер в сообщении",
            "estimated_time": "12 часов"
        }
    return {
        "action": "reply",
        "response_to_user": "Спасибо за информацию. Оператор скоро свяжется с вами.",
        "estimated_time": ""
    }


# === Основной вход ===
async def process_ticket(
    *,
    user_message: str,
    history: List[Dict[str, Any]],
    current_theme: Optional[str] = None,
    user_id: int,
    image_bytes: Optional[bytes] = None,
    filename: str = ""
) -> Dict[str, Any]:
    theme = current_theme or "deposit"
    logger.info(f"🆕 Запрос ИИ: user_id={user_id}, тема={theme}, сообщение='{user_message}'")

    # 🔑 1. Анализ изображения (если есть) → кэшируем
    if ENABLE_MEDIA_ANALYSIS and image_bytes:
        try:
            timestamp = history[-1]["timestamp"] if history else "unknown"
            image_summary = await analyze_and_cache_image(image_bytes, user_id, timestamp, filename)
        except Exception as e:
            logger.error(f"❌ Ошибка анализа изображения: {e}")
            image_summary = "[Изображение: ошибка анализа]"

    # 🔑 2. Подготовка истории: изображения → summary
    prepared_history = await prepare_history_for_prompt(history, user_id)

    # 🔑 3. Формируем промпт из файла
    prompts = load_prompts()
    prompt = prompts["gemini_main_prompt_template"].format(
        user_id=user_id,
        theme=theme,
        history=prepared_history,
        user_message=user_message
    )

    # 🔑 4. Подготовка контента
    if ENABLE_MEDIA_ANALYSIS and image_bytes:
        try:
            mime_type = determine_mime_type(image_bytes, filename)
            logger.info(f"🖼️ Медиа: {len(image_bytes)} байт, MIME={mime_type}")
            contents = [
                {"mime_type": mime_type, "data": image_bytes},
                prompt
            ]
        except Exception as e:
            logger.error(f"❌ Ошибка подготовки медиа: {e}")
            contents = [prompt]
    else:
        contents = [prompt]

    # 🔑 5. Вызов ИИ
    ai_result = await _call_gemini_with_contents(contents)
    if ai_result:
        return ai_result

    logger.warning("⚠️ Используется fallback-логика")
    return _fallback_response(user_message, theme)