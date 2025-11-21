# config.py
import os
from typing import List

# Безопасная загрузка с проверкой
def _get_env(key: str, default=None, cast=str):
    value = os.getenv(key)
    if value is None:
        if default is not None:
            return default
        raise ValueError(f"Переменная окружения {key} обязательна, но не найдена.")
    try:
        return cast(value)
    except Exception as e:
        raise ValueError(f"Невозможно преобразовать {key}={value!r} в {cast.__name__}: {e}")

BOT_TOKEN = _get_env("BOT_TOKEN")
SUPPORT_GROUP_ID = _get_env("SUPPORT_GROUP_ID", cast=int)
ADMINS = [
    int(x.strip()) for x in _get_env("ADMINS", "").split(",") if x.strip()
]
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # опционально