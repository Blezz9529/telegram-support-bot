# services/widget_session.py
"""
Управление сессиями виджета (сайт)
"""
import aiosqlite
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

DB_PATH = "data/support.db"


async def init_widget_db():
    """Инициализация таблиц для виджета"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица сессий виджета
        await db.execute("""
            CREATE TABLE IF NOT EXISTS widget_sessions (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER,
                site_user_id TEXT,
                username TEXT,
                full_name TEXT,
                state TEXT,
                feedback_type TEXT,
                closed_at TIMESTAMP,
                created_at TIMESTAMP,
                last_activity TIMESTAMP,
                ai_paused_until TIMESTAMP,
                ai_generation INTEGER DEFAULT 0,
                topic_id INTEGER,
                theme TEXT,
                is_blocked BOOLEAN DEFAULT 0
            )
        """)
        
        # Таблица сообщений виджета
        await db.execute("""
            CREATE TABLE IF NOT EXISTS widget_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                text TEXT,
                sender TEXT CHECK(sender IN ('user', 'operator')),
                timestamp TIMESTAMP,
                attachment_url TEXT,
                attachment_type TEXT,
                attachment_name TEXT
            )
        """)
        
        # Индексы для скорости
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_widget_messages_session 
            ON widget_messages(session_id, timestamp)
        """)
        
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_widget_sessions_user 
            ON widget_sessions(user_id)
        """)

        # 🔑 Безопасное добавление колонок в widget_sessions
        cursor = await db.execute("PRAGMA table_info(widget_sessions)")
        columns = await cursor.fetchall()
        column_names = {col[1] for col in columns}
        if "site_user_id" not in column_names:
            await db.execute("ALTER TABLE widget_sessions ADD COLUMN site_user_id TEXT")
        if "state" not in column_names:
            await db.execute("ALTER TABLE widget_sessions ADD COLUMN state TEXT")
        if "feedback_type" not in column_names:
            await db.execute("ALTER TABLE widget_sessions ADD COLUMN feedback_type TEXT")
        if "closed_at" not in column_names:
            await db.execute("ALTER TABLE widget_sessions ADD COLUMN closed_at TIMESTAMP")
        if "ai_paused_until" not in column_names:
            await db.execute("ALTER TABLE widget_sessions ADD COLUMN ai_paused_until TIMESTAMP")
        if "ai_generation" not in column_names:
            await db.execute("ALTER TABLE widget_sessions ADD COLUMN ai_generation INTEGER DEFAULT 0")
            await db.execute("UPDATE widget_sessions SET ai_generation = 0 WHERE ai_generation IS NULL")

        cursor = await db.execute("PRAGMA table_info(widget_messages)")
        message_columns = await cursor.fetchall()
        message_column_names = {col[1] for col in message_columns}
        if "attachment_name" not in message_column_names:
            await db.execute("ALTER TABLE widget_messages ADD COLUMN attachment_name TEXT")

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_widget_sessions_site_user
            ON widget_sessions(site_user_id)
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_widget_sessions_topic
            ON widget_sessions(topic_id)
        """)
        
        # 🔑 Инициализация значений по умолчанию
        await db.execute("""
            UPDATE widget_sessions SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL
        """)
        await db.execute("""
            UPDATE widget_sessions SET last_activity = CURRENT_TIMESTAMP WHERE last_activity IS NULL
        """)
        await db.execute("""
            UPDATE widget_messages SET timestamp = CURRENT_TIMESTAMP WHERE timestamp IS NULL
        """)
        
        await db.commit()
        logger.info("✅ Таблицы виджета инициализированы")


async def create_session(
    user_id: Optional[int] = None,
    site_user_id: Optional[str] = None,
    username: str = "",
    full_name: str = ""
) -> str:
    """Создаёт новую сессию виджета. Возвращает session_id."""
    session_id = str(uuid.uuid4())
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO widget_sessions (session_id, user_id, site_user_id, username, full_name)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, user_id, site_user_id, username, full_name))
        await db.commit()
    
    logger.info(f"🌐 Создана сессия виджета: {session_id[:8]}... (user_id={user_id})")
    return session_id


async def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Получает сессию по ID"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM widget_sessions WHERE session_id = ?",
            (session_id,)
        )
        row = await cursor.fetchone()
        
        if row:
            session = dict(row)
            # Обновляем last_activity
            await db.execute(
                "UPDATE widget_sessions SET last_activity = CURRENT_TIMESTAMP WHERE session_id = ?",
                (session_id,)
            )
            await db.commit()
            return session
        return None


async def link_session_to_user(session_id: str, user_id: int) -> bool:
    """Привязывает сессию к пользователю Telegram"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE widget_sessions SET user_id = ? WHERE session_id = ?",
            (user_id, session_id)
        )
        await db.commit()
    
    logger.info(f"🔗 Сессия {session_id[:8]}... привязана к user_id={user_id}")
    return True


async def get_session_messages(session_id: str, limit: int = 50) -> list:
    """Получает историю сообщений сессии"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM widget_messages 
            WHERE session_id = ? 
            ORDER BY timestamp ASC 
            LIMIT ?
            """,
            (session_id, limit)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def save_widget_message(
    session_id: str,
    text: str,
    sender: str,
    attachment_url: Optional[str] = None,
    attachment_type: Optional[str] = None,
    attachment_name: Optional[str] = None
) -> int:
    """Сохраняет сообщение в БД. Возвращает ID сообщения."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO widget_messages (session_id, text, sender, attachment_url, attachment_type, attachment_name)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, text, sender, attachment_url, attachment_type, attachment_name)
        )
        message_id = cursor.lastrowid
        await db.commit()
    
    return message_id


async def update_session_topic(session_id: str, topic_id: int, theme: str) -> bool:
    """Обновляет topic_id и theme для сессии"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE widget_sessions SET topic_id = ?, theme = ? WHERE session_id = ?",
            (topic_id, theme, session_id)
        )
        await db.commit()
    return True


async def set_session_site_user_id(session_id: str, site_user_id: str) -> bool:
    """Привязывает site_user_id к сессии"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE widget_sessions SET site_user_id = ? WHERE session_id = ?",
            (site_user_id, session_id)
        )
        await db.commit()
    return True


async def set_session_state(session_id: str, state: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE widget_sessions SET state = ? WHERE session_id = ?",
            (state, session_id)
        )
        await db.commit()
    return True


async def set_session_theme(session_id: str, theme: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE widget_sessions SET theme = ? WHERE session_id = ?",
            (theme, session_id)
        )
        await db.commit()
    return True


async def set_session_feedback_type(session_id: str, feedback_type: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE widget_sessions SET feedback_type = ? WHERE session_id = ?",
            (feedback_type, session_id)
        )
        await db.commit()
    return True


async def close_session(session_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE widget_sessions SET state = ?, closed_at = CURRENT_TIMESTAMP, topic_id = NULL, theme = NULL WHERE session_id = ?",
            ("closed", session_id)
        )
        await db.commit()
    return True


async def purge_expired_session_content(session_id: str) -> bool:
    """Удаляет историю просроченной сессии и сбрасывает её к стартовому состоянию."""
    from services.conversation_store import clear_conversation_events

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM widget_messages WHERE session_id = ?",
            (session_id,)
        )
        await db.execute(
            """
            UPDATE widget_sessions
            SET topic_id = NULL,
                theme = NULL,
                feedback_type = NULL,
                state = 'choosing_theme',
                ai_paused_until = NULL,
                ai_generation = 0,
                closed_at = CURRENT_TIMESTAMP,
                last_activity = CURRENT_TIMESTAMP
            WHERE session_id = ?
            """,
            (session_id,)
        )
        await db.commit()
    await clear_conversation_events(f"widget:{session_id}")
    logger.info(f"🧹 Очищена просроченная сессия виджета: {session_id[:8]}...")
    return True


async def get_session_by_topic_id(topic_id: int) -> Optional[Dict[str, Any]]:
    """Находит сессию по topic_id"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM widget_sessions WHERE topic_id = ?",
            (topic_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_sessions_by_site_user_id(site_user_id: str) -> list:
    """Находит все сессии по site_user_id"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM widget_sessions WHERE site_user_id = ? ORDER BY last_activity DESC",
            (site_user_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_latest_session_by_site_user_id(site_user_id: str) -> Optional[Dict[str, Any]]:
    """Находит последнюю активную сессию по site_user_id"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM widget_sessions WHERE site_user_id = ? ORDER BY last_activity DESC LIMIT 1",
            (site_user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_session_by_user_id(user_id: int) -> Optional[Dict[str, Any]]:
    """Находит активную сессию по user_id"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM widget_sessions 
            WHERE user_id = ? 
            ORDER BY last_activity DESC 
            LIMIT 1
            """,
            (user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def block_session(session_id: str) -> bool:
    """Блокирует сессию"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE widget_sessions SET is_blocked = 1 WHERE session_id = ?",
            (session_id,)
        )
        await db.commit()
    return True


async def block_sessions_by_site_user_id(site_user_id: str) -> int:
    """Блокирует все сессии по site_user_id"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE widget_sessions SET is_blocked = 1 WHERE site_user_id = ?",
            (site_user_id,)
        )
        await db.commit()
        return cursor.rowcount


async def unblock_sessions_by_site_user_id(site_user_id: str) -> int:
    """Разблокирует все сессии по site_user_id"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE widget_sessions SET is_blocked = 0 WHERE site_user_id = ?",
            (site_user_id,)
        )
        await db.commit()
        return cursor.rowcount


async def is_site_user_blocked(site_user_id: str) -> bool:
    """Проверяет, есть ли заблокированные сессии по site_user_id"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM widget_sessions WHERE site_user_id = ? AND is_blocked = 1",
            (site_user_id,)
        )
        row = await cursor.fetchone()
        return bool(row and row[0] > 0)


async def cleanup_old_sessions(max_days: int = 30) -> int:
    """Удаляет старые сессии. Возвращает количество удалённых."""
    cutoff = datetime.now() - timedelta(days=max_days)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM widget_sessions WHERE last_activity < ?",
            (cutoff.isoformat(),)
        )
        deleted = cursor.rowcount
        await db.commit()
    
    if deleted:
        logger.info(f"🗑️ Удалено {deleted} старых сессий виджета")
    return deleted
