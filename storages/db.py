# storages/db.py
import aiosqlite
import os
import shutil
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

DB_PATH = "data/support.db"
BACKUP_DIR = "data/backups"

os.makedirs("data", exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Создаём таблицу, если не существует (БЕЗ DEFAULT для TIMESTAMP)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                topic_id INTEGER,
                theme TEXT,
                is_blocked BOOLEAN DEFAULT 0,
                last_message_id INTEGER DEFAULT 0,
                first_message_in_ticket BOOLEAN DEFAULT 1,
                feedback_type TEXT,
                last_activity TIMESTAMP
            )
        """)

        # 🔑 Безопасное добавление колонок (без гонки)
        cursor = await db.execute("PRAGMA table_info(users)")
        columns = await cursor.fetchall()
        column_names = {col[1] for col in columns}

        if "first_message_in_ticket" not in column_names:
            await db.execute("ALTER TABLE users ADD COLUMN first_message_in_ticket BOOLEAN DEFAULT 1")
        
        # 🔑 Добавляем поле для типа отзыва
        if "feedback_type" not in column_names:
            await db.execute("ALTER TABLE users ADD COLUMN feedback_type TEXT DEFAULT NULL")
        
        # 🔑 Добавляем поле для последней активности
        if "last_activity" not in column_names:
            # SQLite не позволяет DEFAULT CURRENT_TIMESTAMP при ALTER TABLE
            # Добавляем без default, потом обновляем существующие записи
            await db.execute("ALTER TABLE users ADD COLUMN last_activity TIMESTAMP")
            await db.execute("UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE last_activity IS NULL")
            logger.info("✅ Добавлена колонка last_activity")

        if "ai_paused_until" not in column_names:
            await db.execute("ALTER TABLE users ADD COLUMN ai_paused_until TIMESTAMP")

        if "ai_generation" not in column_names:
            await db.execute("ALTER TABLE users ADD COLUMN ai_generation INTEGER DEFAULT 0")
            await db.execute("UPDATE users SET ai_generation = 0 WHERE ai_generation IS NULL")

        # Таблица для постоянного соответствия клиент -> форумный топик
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_topics (
                client_key TEXT PRIMARY KEY,
                topic_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

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
    
    # 🔑 Автоматически обновляем last_activity
    kwargs['last_activity'] = datetime.now().isoformat()
    
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {fields} WHERE user_id = ?", values)
        await db.commit()


# ===== Persistent forum topic mapping =====
async def get_persistent_topic(client_key: str) -> Optional[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT topic_id FROM forum_topics WHERE client_key = ?",
            (client_key,)
        )
        row = await cur.fetchone()
        return int(row["topic_id"]) if row else None


async def set_persistent_topic(client_key: str, topic_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "REPLACE INTO forum_topics (client_key, topic_id, created_at) VALUES (?, ?, ?)",
            (client_key, int(topic_id), datetime.now().isoformat())
        )
        await db.commit()


# 🔑 Очистка старых записей (опционально, для обслуживания)
async def cleanup_old_users(max_days: int = 30):
    """Удаляет записи старше max_days дней (для обслуживания БД)"""
    cutoff_date = datetime.now() - timedelta(days=max_days)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM users WHERE last_activity < ?",
            (cutoff_date.isoformat(),)
        )
        deleted = cursor.rowcount
        await db.commit()
        logger.info(f"🗑️ Удалено {deleted} старых записей")
        return deleted


# 🔑 БЭКАПЫ И ВОССТАНОВЛЕНИЕ

async def backup_database(backup_name: str = None) -> str:
    """Создаёт бэкап БД. Возвращает путь к файлу."""
    if backup_name is None:
        backup_name = f"support_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    
    try:
        # Проверяем целостность перед бэкапом
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA integrity_check")
            logger.info("✅ Проверка целостности пройдена")
        
        # Копируем файл
        shutil.copy2(DB_PATH, backup_path)
        logger.info(f"💾 Бэкап создан: {backup_path}")
        
        # 🔑 Удаляем старые бэкапы (храним последние 10)
        await cleanup_old_backups(max_backups=10)
        
        return backup_path
    except Exception as e:
        logger.error(f"❌ Ошибка бэкапа: {e}")
        raise


async def restore_database(backup_path: str) -> bool:
    """Восстанавливает БД из бэкапа. Возвращает True если успешно."""
    if not os.path.exists(backup_path):
        logger.error(f"❌ Бэкап не найден: {backup_path}")
        return False
    
    try:
        # Создаём бэкап текущей БД перед восстановлением
        await backup_database()
        
        # Восстанавливаем
        shutil.copy2(backup_path, DB_PATH)
        logger.info(f"✅ Восстановлено из: {backup_path}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка восстановления: {e}")
        return False


async def cleanup_old_backups(max_backups: int = 10):
    """Удаляет старые бэкапы, оставляя только max_backups последних."""
    try:
        backups = sorted([
            f for f in os.listdir(BACKUP_DIR) 
            if f.startswith('support_backup_') and f.endswith('.db')
        ])
        
        if len(backups) > max_backups:
            to_delete = backups[:-max_backups]
            for backup in to_delete:
                os.remove(os.path.join(BACKUP_DIR, backup))
                logger.info(f"🗑️ Удалён старый бэкап: {backup}")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка очистки бэкапов: {e}")


async def get_backup_list() -> list:
    """Возвращает список доступных бэкапов."""
    try:
        backups = sorted([
            {
                'name': f,
                'path': os.path.join(BACKUP_DIR, f),
                'size': os.path.getsize(os.path.join(BACKUP_DIR, f)),
                'created': datetime.fromtimestamp(
                    os.path.getctime(os.path.join(BACKUP_DIR, f))
                ).strftime('%Y-%m-%d %H:%M')
            }
            for f in os.listdir(BACKUP_DIR)
            if f.startswith('support_backup_') and f.endswith('.db')
        ], key=lambda x: x['name'], reverse=True)
        return backups
    except Exception as e:
        logger.error(f"❌ Ошибка получения списка бэкапов: {e}")
        return []


async def get_db_stats() -> dict:
    """Возвращает статистику БД."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # Общее количество пользователей
        cursor = await db.execute("SELECT COUNT(*) as count FROM users")
        total_users = (await cursor.fetchone())['count']
        
        # Активные за последние 24 часа
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        cursor = await db.execute(
            "SELECT COUNT(*) as count FROM users WHERE last_activity > ?",
            (cutoff,)
        )
        active_users = (await cursor.fetchone())['count']
        
        # Заблокированные
        cursor = await db.execute("SELECT COUNT(*) as count FROM users WHERE is_blocked = 1")
        blocked_users = (await cursor.fetchone())['count']
        
        # Размер файла
        db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
        
        return {
            'total_users': total_users,
            'active_users_24h': active_users,
            'blocked_users': blocked_users,
            'db_size_bytes': db_size,
            'db_size_mb': round(db_size / 1024 / 1024, 2)
        }
