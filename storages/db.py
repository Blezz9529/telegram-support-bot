# storages/db.py
import aiosqlite
import os
from typing import Optional, Dict, Any

DB_PATH = "data/support.db"

os.makedirs("data", exist_ok=True)

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                topic_id INTEGER,
                theme TEXT,
                is_blocked BOOLEAN DEFAULT 0,
                last_message_id INTEGER DEFAULT 0,
                first_message_in_ticket BOOLEAN DEFAULT 1
            )
        """)
        # Добавляем колонку, если БД уже существовала без неё
        try:
            await db.execute("ALTER TABLE users ADD COLUMN first_message_in_ticket BOOLEAN DEFAULT 1")
        except aiosqlite.OperationalError:
            pass  # колонка уже есть
        await db.commit()

async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Получает пользователя по ID. Возвращает dict или None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row  # чтобы можно было по ключам
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

async def create_user(user_id: int, username: str = "", full_name: str = ""):
    """Создаёт пользователя, если не существует."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO users (user_id, username, full_name, is_blocked, first_message_in_ticket)
            VALUES (?, ?, ?, 0, 1)
        """, (user_id, username or "", full_name or ""))
        await db.commit()

async def update_user(user_id: int, **kwargs):
    """Обновляет поля пользователя. Пример: update_user(123, is_blocked=True)"""
    if not kwargs:
        return
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {fields} WHERE user_id = ?", values)
        await db.commit()

# Экспортируем всё явно (на случай, если кто-то делает from ... import *)
__all__ = ["init_db", "get_user", "create_user", "update_user"]