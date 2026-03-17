# services/ai_agent.py
import os
import json
import logging
import asyncio
import random
import re
import base64
from typing import Any, Dict, List, Optional
from threading import Lock

# Сначала создаём logger
logger = logging.getLogger(__name__)
ENABLE_MEDIA_ANALYSIS = True

try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
    logger.info("✅ OpenAI библиотека загружена")
except ImportError as e:
    _OPENAI_AVAILABLE = False
    logger.critical(f"❌ openai не установлен: {e!r}")

try:
    import imghdr
    _IMGHDR_AVAILABLE = True
except ImportError:
    _IMGHDR_AVAILABLE = False

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
                topics = data.get("TOPIC_HANDLING", [])
                examples = data.get("EXAMPLES", {})
                
                persona = core.get("persona", "ИИ-агент поддержки")
                tone = core.get("tone", "кратко, по делу")
                lang = core.get("language", "Русский")
                max_sentences = strict.get("max_sentences", 3)
                operator_prefix = core.get("operator_prefix", "[OPERATOR]")
                
                # 🔑 Формируем базовую инструкцию
                system_instruction = (
                    f"Ты — {persona}. Язык: {lang}. Тон: {tone}. "
                    f"Максимум {max_sentences} предложения в ответе. "
                    f"Если нужна помощь оператора — начни ответ с {operator_prefix}.\n\n"
                )
                
                # 🔑 ПРАВИЛА ПОВЕДЕНИЯ — ЗАПРЕТЫ
                system_instruction += (
                    "🔒 СТРОГИЕ ЗАПРЕТЫ:\n"
                    "• НЕ сообщай пользователю о передаче оператору или менеджеру\n"
                    "• НЕ упоминай внутренние процессы (эскалация, заявка, тикет, статус)\n"
                    "• НЕ пиши что данные некорректные/неправильные — проси прислать ещё раз\n"
                    "• НЕ говори о системных действиях (создаю топик, фиксирую, передаю)\n"
                    "• Веди себя как живой человек — оператор поддержки в переписке\n"
                    "• Если запрос требует участия оператора — просто начни с [OPERATOR] и напиши человеческий ответ\n"
                    "\n"
                )
                
                # 🔑 ПРИМЕРЫ ПРАВИЛЬНЫХ ОТВЕТОВ
                system_instruction += (
                    "📝 ПРИМЕРЫ:\n"
                    "❌ НЕЛЬЗЯ: \"Передаю ваш запрос оператору\"\n"
                    "✅ МОЖНО: \"[OPERATOR] Приняла, сейчас проверю вашу информацию\"\n"
                    "\n"
                    "❌ НЕЛЬЗЯ: \"Данные некорректные, создаю заявку\"\n"
                    "✅ МОЖНО: \"Вижу, что чек нечёткий. Пришлите, пожалуйста, ещё раз\"\n"
                    "\n"
                    "❌ НЕЛЬЗЯ: \"Ваша заявка передана менеджеру\"\n"
                    "✅ МОЖНО: \"Приняла. Ожидайте, скоро вернусь с ответом\"\n"
                    "\n"
                )
                
                # 🔑 Добавляем проверку на дубликаты
                duplicate_instruction = data.get("DUPLICATE_CHECK_INSTRUCTION", "")
                if duplicate_instruction:
                    system_instruction += f"\n{duplicate_instruction}\n"
                
                # 🔑 Добавляем правила поведения
                avoid_words = strict.get("avoid_words", [])
                if avoid_words:
                    system_instruction += f"🚫 НЕ используй слова: {', '.join(avoid_words)}.\n"
                
                forbidden_styles = strict.get("forbidden_styles", [])
                if forbidden_styles:
                    system_instruction += f"🚫 НЕ используй: {', '.join(forbidden_styles)}.\n"
                
                one_step = strict.get("one_step_per_message", "")
                if one_step:
                    system_instruction += f"📌 {one_step}\n"
                
                no_repeated = strict.get("no_repeated_requests", "")
                if no_repeated:
                    system_instruction += f"📌 {no_repeated}\n"
                
                # 🔑 Добавляем правила по оператору
                operator_format = operator.get("operator_format", "")
                if operator_format:
                    system_instruction += f"\n{operator_format}\n"
                
                # 🔑 Добавляем правила по темам (TOPIC_HANDLING)
                if topics:
                    system_instruction += "\n=== ПРАВИЛА ПО ТЕМАМ ===\n"
                    for topic_rule in topics:
                        topic_name = topic_rule.get("topic", "Неизвестная тема")
                        operator_rule = topic_rule.get("operator", "AFTER_INFO")
                        
                        system_instruction += f"\n📍 {topic_name} (правило: {operator_rule}):\n"
                        
                        # Логика для разных типов правил
                        if "logic" in topic_rule:
                            logic = topic_rule["logic"]
                            system_instruction += f"   • Логика: {' '.join(logic)}\n"
                        
                        if "cases" in topic_rule:
                            cases = topic_rule["cases"]
                            system_instruction += "   • Кейсы:\n"
                            for case, action in cases.items():
                                system_instruction += f"     - {case}: {action}\n"
                        
                        if "instruction" in topic_rule:
                            instruction = topic_rule["instruction"]
                            system_instruction += f"   • Инструкция: {instruction}\n"
                        
                        if "steps" in topic_rule:
                            steps = topic_rule["steps"]
                            system_instruction += "   • Шаги:\n"
                            for i, step in enumerate(steps, 1):
                                system_instruction += f"     {i}. {step}\n"
                
                # 🔑 Добавляем примеры (EXAMPLES)
                if examples:
                    system_instruction += "\n=== ПРИМЕРЫ ДИАЛОГОВ ===\n"
                    for example_name, example_dialog in examples.items():
                        if isinstance(example_dialog, list) and len(example_dialog) > 0:
                            # Это диалог с примерами
                            system_instruction += f"\n📘 {example_name}:\n"
                            for msg in example_dialog:
                                if isinstance(msg, dict):
                                    user_text = msg.get("user", "")
                                    assistant_text = msg.get("assistant", "")
                                    if user_text and assistant_text:
                                        system_instruction += f"   Пользователь: {user_text}\n"
                                        system_instruction += f"   Ассистент: {assistant_text}\n"
                        elif isinstance(example_dialog, list) and len(example_dialog) > 0:
                            # Это список неправильных примеров
                            system_instruction += f"\n🚫 {example_name} (НЕЛЬЗЯ ТАК):\n"
                            for wrong_example in example_dialog:
                                system_instruction += f"   • {wrong_example}\n"
                
                # 🔑 Добавляем правила анализа изображений
                if "IMAGE_ANALYSIS" in data:
                    img = data["IMAGE_ANALYSIS"]
                    img_format = img.get("format", "1-2 предложения")
                    reqs = img.get("requirements", [])
                    system_instruction += f"\n\n=== АНАЛИЗ ИЗОБРАЖЕНИЙ ===\n"
                    system_instruction += f"Описывай изображение в {img_format}. Укажи: {', '.join(reqs)}.\n"

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


# === Инициализация клиента OpenRouter ===
_openrouter_client = None

def _get_openrouter_client() -> Optional["OpenAI"]:
    global _openrouter_client
    if not _OPENAI_AVAILABLE:
        logger.error("❌ OpenAI библиотека недоступна")
        return None
    if _openrouter_client is None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            logger.error("❌ OPENROUTER_API_KEY не задан")
            return None
        try:
            base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            model_name = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite")
            
            _openrouter_client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                default_headers={
                    "HTTP-Referer": "https://github.com/telegram-support-bot",
                    "X-OpenRouter-Title": "Telegram Support Bot"
                }
            )
            logger.info(f"✅ OpenRouter: клиент инициализирован (модель: {model_name}, URL: {base_url})")
        except Exception as e:
            logger.error(f"❌ OpenRouter: ошибка инициализации: {e}")
            return None
    return _openrouter_client


# === Очистка ответа (исправлена: удаляет [OPERATOR] из любого места) ===
def clean_gemini_response(text: str) -> tuple[str, bool]:
    try:
        data = json.loads(text.strip())
        
        # 🔑 OpenRouter JSON формат: {"text": "...", "is_operator": false}
        if isinstance(data, dict):
            if "text" in data:
                text = str(data["text"])
            elif "response" in data:
                text = str(data["response"])
            elif "answer" in data:
                text = str(data["answer"])
            elif "content" in data:
                text = str(data["content"])
            elif "message" in data:
                text = str(data["message"])
        
        # 🔑 Если массив — берём первый элемент
        elif isinstance(data, list) and len(data) > 0:
            first_item = data[0]
            if isinstance(first_item, dict):
                if "text" in first_item:
                    text = str(first_item["text"])
                elif "response" in first_item:
                    text = str(first_item["response"])
                else:
                    text = str(first_item)
            else:
                text = str(first_item)
    except:
        pass
    
    # Очищаем от управляющих символов
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text).strip()

    # 🔑 УДАЛЯЕМ [OPERATOR] из любого места в тексте
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

    client = _get_openrouter_client()
    if not client:
        summary = "[Изображение: OpenRouter недоступен]"
        logger.warning(f"🖼️ Изображение {cache_key}: OpenRouter клиент не инициализирован")
    else:
        prompts = load_prompts()
        prompt = prompts["gemini_image_analysis_prompt"]
        
        # Кодируем изображение в base64
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        mime_type = determine_mime_type(image_bytes, filename)
        model_name = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite")
        
        logger.info(f"🖼️ Анализ изображения {cache_key}: {len(image_bytes)} байт, MIME={mime_type}, модель={model_name}")
        
        try:
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=model_name,
                messages=[
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
                    ]}
                ],
                max_tokens=256
            )
            summary = (response.choices[0].message.content or "[Изображение: не удалось получить описание]").strip()
            logger.info(f"🖼️ Изображение {cache_key} проанализировано. Результат: {summary}")
        except Exception as e:
            logger.error(f"❌ Ошибка анализа изображения {cache_key}: {e}")
            summary = "[Изображение: ошибка анализа]"

    with _cache_lock:
        _image_summaries[cache_key] = summary
    return summary


# === Подготовка истории: изображения → summary ===
async def prepare_history_for_prompt(
    original_history: List[Dict[str, Any]],
    user_id: int
) -> List[Dict[str, Any]]:
    prepared = []
    for msg in original_history:
        if msg.get("has_media") and msg.get("from_user"):
            timestamp = msg.get("timestamp", "unknown")
            img_key = (user_id, timestamp)
            summary = _image_summaries.get(img_key, "[Изображение: описание недоступно]")
            prepared.append({
                "from_user": True,
                "text": f"[ИЗОБРАЖЕНИЕ]\n{summary}",
                "has_media": False,
                "timestamp": timestamp
            })
        else:
            prepared.append(msg)
    return prepared


# === Вызов модели без orchestration retry ===
async def _call_openrouter_with_messages(
    messages: List[Dict[str, Any]], 
    estimated_time: str = ""  # 🔑 Время обработки из промпта
) -> Optional[Dict[str, Any]]:
    client = _get_openrouter_client()
    if not client:
        logger.error("❌ OpenRouter клиент не инициализирован")
        return None

    model_name = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite")
    temperature = float(os.getenv("GEMINI_TEMPERATURE", "0.1"))
    max_tokens = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "768"))

    # ✅ ЛОГИРУЕМ ПРОМПТ
    logger.info(f"📤 OpenRouter: промпт (полный):\n{messages}")
    logger.info(f"📤 OpenRouter: параметры — модель={model_name}, temp={temperature}, tokens={max_tokens}")

    logger.info("🔄 OpenRouter: одиночный вызов без orchestration retry")

    request_params = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    logger.info(f"🔍 DEBUG: запрос к API: {json.dumps(request_params, ensure_ascii=False)[:500]}...")

    response = await asyncio.to_thread(
        client.chat.completions.create,
        model=model_name,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens
    )

    logger.info(f"🔍 DEBUG: полный ответ API: choices={len(response.choices) if response.choices else 0}")

    if not response.choices or not response.choices[0].message.content:
        logger.warning("❌ OpenRouter: пустой ответ")
        raise RuntimeError("OpenRouter returned empty response")

    raw_text = response.choices[0].message.content
    logger.info(f"📥 OpenRouter: ответ (сырой):\n{raw_text}")
    logger.info(f"🔍 DEBUG: тип raw_text={type(raw_text)}, длина={len(raw_text) if raw_text else 0}")

    if hasattr(response, 'usage') and response.usage:
        logger.info(
            "📊 OpenRouter: токены — prompt=%s, completion=%s, total=%s",
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            response.usage.total_tokens,
        )

    clean_text, needs_escalation = clean_gemini_response(raw_text)
    logger.info(f"✅ OpenRouter: очищенный ответ: {clean_text}, эскалация: {needs_escalation}")

    action = "escalate" if needs_escalation else "reply"
    estimate = estimated_time if needs_escalation else ""

    return {
        "action": action,
        "response_to_user": clean_text,
        "escalation_reason": "нужна помощь оператора" if needs_escalation else None,
        "estimated_time": estimate
    }


# === Получение estimated_time из промпта по теме ===
def get_estimated_time_for_theme(theme: str) -> str:
    """Возвращает время обработки для темы из промптов"""
    try:
        with open("locales/prompts.json", "r", encoding="utf-8") as f:
            data = json.loads(f.read())
            topics = data.get("TOPIC_HANDLING", [])
            for topic_rule in topics:
                if topic_rule.get("topic_key") == theme or topic_rule.get("topic") == theme:
                    return topic_rule.get("estimated_time") or ""
    except:
        pass
    return ""


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

    # 🔑 0. Получаем estimated_time для темы из промпта
    estimated_time = get_estimated_time_for_theme(theme)
    logger.info(f"🕒 Время обработки для темы {theme}: {estimated_time}")

    # 🔑 1. Анализ изображения (если есть) → кэшируем
    if ENABLE_MEDIA_ANALYSIS and image_bytes:
        try:
            timestamp = history[-1]["timestamp"] if history else "unknown"
            image_summary = await analyze_and_cache_image(image_bytes, user_id, timestamp, filename)
            logger.info(f"🖼️ Результат анализа изображения: {image_summary}")
        except Exception as e:
            logger.error(f"❌ Ошибка анализа изображения: {e}")
            image_summary = "[Изображение: ошибка анализа]"

    # 🔑 2. Подготовка истории: изображения → summary
    prepared_history = await prepare_history_for_prompt(history, user_id)

    # 🔑 3. Формируем промпт из файла
    prompts = load_prompts()
    system_instruction = prompts["gemini_system_instruction"]
    main_prompt_template = prompts["gemini_main_prompt_template"]

    user_prompt = main_prompt_template.format(
        user_id=user_id,
        theme=theme,
        history=prepared_history,
        user_message=user_message
    )
    
    logger.info(f"📝 System instruction: {system_instruction[:300]}...")

    # 🔑 4. Подготовка сообщений для OpenRouter
    messages = [{"role": "system", "content": system_instruction}]
    
    # Добавляем изображение в сообщение если есть
    if ENABLE_MEDIA_ANALYSIS and image_bytes:
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        mime_type = determine_mime_type(image_bytes, filename)
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
            ]
        })
        logger.info(f"🖼️ Сообщение с изображением: {len(image_bytes)} байт, MIME={mime_type}")
    else:
        messages.append({"role": "user", "content": user_prompt})
        logger.info(f"💬 Текстовое сообщение: {len(user_prompt)} символов")

    # 🔑 5. Вызов ИИ с estimated_time
    ai_result = await _call_openrouter_with_messages(messages, estimated_time)
    if not ai_result:
        raise RuntimeError("AI agent returned empty result")

    logger.info(
        "✅ ИИ ответ: action=%s, response=%s..., time=%s",
        ai_result["action"],
        ai_result["response_to_user"][:50],
        ai_result.get("estimated_time", ""),
    )
    return ai_result
