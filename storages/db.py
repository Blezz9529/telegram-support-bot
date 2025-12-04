# storages/db.py
import aiosqlite
import os
from typing import Optional, Dict, Any

DB_PATH = "data/support.db"

os.makedirs("data", exist_ok=True)


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Создаём таблицу, если не существует
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

        # 🔑 Безопасное добавление колонки (без гонки)
        cursor = await db.execute("PRAGMA table_info(users)")
        columns = await cursor.fetchall()
        column_names = {col[1] for col in columns}

        if "first_message_in_ticket" not in column_names:
            await db.execute("ALTER TABLE users ADD COLUMN first_message_in_ticket BOOLEAN DEFAULT 1")

        await db.commit()


async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Получает пользователя по ID. Возвращает dict или None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row  # для доступа по ключам
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        user = dict(row)
        # 🔑 Конвертируем INT → BOOL (SQLite хранит BOOLEAN как 0/1)
        user["is_blocked"] = bool(user["is_blocked"])
        user["first_message_in_ticket"] = bool(user["first_message_in_ticket"])
        return user


async def get_user_by_topic_id(topic_id: int) -> Optional[Dict[str, Any]]:
    """Получает пользователя по topic_id. Возвращает dict или None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE topic_id = ?", (topic_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        user = dict(row)
        user["is_blocked"] = bool(user["is_blocked"])
        user["first_message_in_ticket"] = bool(user["first_message_in_ticket"])
        return user


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