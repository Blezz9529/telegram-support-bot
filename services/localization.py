# services/localization.py
import aiofiles
import json
import os
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

LOCALES_DIR = "locales"

# Кэш для всех локализаций
_cached_texts: Dict[str, Dict[str, Any]] = {}


async def _load_json(filename: str) -> Dict[str, Any]:
    """Загружает JSON с кэшированием."""
    if filename in _cached_texts:
        return _cached_texts[filename]
    path = os.path.join(LOCALES_DIR, filename)
    try:
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            content = await f.read()
            data = json.loads(content)
            _cached_texts[filename] = data
            logger.info(f"✅ Локализация {filename} загружена")
            return data
    except FileNotFoundError:
        logger.critical(f"❌ Файл локализации {path} не найден!")
        raise
    except json.JSONDecodeError as e:
        logger.critical(f"❌ Ошибка в формате {path}: {e}")
        raise


async def load_text(key: str, **kwargs) -> str:
    """Загружает текст из texts.json с подстановкой переменных."""
    texts = await _load_json("texts.json")
    template = texts.get(key, f"{{{key}}}")
    return template.format(**kwargs)


async def load_button(category: str, key: str) -> str:
    """Загружает текст кнопки из buttons.json."""
    buttons = await _load_json("buttons.json")
    return buttons.get(category, {}).get(key, f"[{category}.{key}]")


async def load_ai_message(key: str, **kwargs) -> str:
    """Загружает ИИ-сообщение из ai_messages.json."""
    ai_msgs = await _load_json("ai_messages.json")
    template = ai_msgs.get(key, f"{{{key}}}")
    return template.format(**kwargs)


# ✅ НОВОЕ: загрузка промптов
async def load_prompts() -> Dict[str, Any]:
    """Загружает промпты из prompts.json."""
    return await _load_json("prompts.json")


# Экспортируем только используемые функции
__all__ = ["load_text", "load_button", "load_ai_message", "load_prompts"]