# storages/db.py (обновлённая init_db)
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
        # Добавим колонку, если её нет (для уже существующих БД)
        try:
            await db.execute("ALTER TABLE users ADD COLUMN first_message_in_ticket BOOLEAN DEFAULT 1")
        except aiosqlite.OperationalError:
            pass  # уже есть
        await db.commit()