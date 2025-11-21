import aiofiles
import json
import os
from typing import Any, Dict

LOCALES_DIR = "locales"

# Кэшируем локализации
_cached_texts: Dict[str, Dict[str, Any]] = {}

async def _load_json(filename: str) -> Dict[str, Any]:
    if filename in _cached_texts:
        return _cached_texts[filename]
    path = os.path.join(LOCALES_DIR, filename)
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        content = await f.read()
        data = json.loads(content)
        _cached_texts[filename] = data
        return data

async def load_text(key: str, **kwargs) -> str:
    texts = await _load_json("texts.json")
    template = texts.get(key, f"{{{key}}}")
    return template.format(**kwargs)

async def load_button(category: str, key: str) -> str:
    buttons = await _load_json("buttons.json")
    return buttons.get(category, {}).get(key, f"[{category}.{key}]")

async def load_ai_message(key: str, **kwargs) -> str:
    ai_msgs = await _load_json("ai_messages.json")
    template = ai_msgs.get(key, f"{{{key}}}")
    return template.format(**kwargs)