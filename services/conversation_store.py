import aiosqlite
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

DB_PATH = "data/support.db"


def parse_conversation_key(conversation_key: str) -> Tuple[str, str]:
    if ":" not in conversation_key:
        raise ValueError(f"Invalid conversation key: {conversation_key}")
    channel, subject = conversation_key.split(":", 1)
    return channel, subject


async def init_conversation_store() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_key TEXT NOT NULL,
                channel TEXT NOT NULL,
                actor TEXT NOT NULL,
                text TEXT NOT NULL,
                attachment_type TEXT,
                attachment_name TEXT,
                attachment_kind TEXT,
                visible_to_ai BOOLEAN NOT NULL DEFAULT 1,
                forum_status TEXT NOT NULL DEFAULT 'pending',
                forum_message_id INTEGER,
                batch_id INTEGER,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_events_key_created
            ON conversation_events(conversation_key, created_at, id)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_events_batch
            ON conversation_events(conversation_key, batch_id, actor)
            """
        )
        await db.commit()


async def append_event(
    *,
    conversation_key: str,
    channel: str,
    actor: str,
    text: str,
    attachment_type: Optional[str] = None,
    attachment_name: Optional[str] = None,
    attachment_kind: Optional[str] = None,
    visible_to_ai: bool = True,
    forum_status: str = "pending",
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO conversation_events (
                conversation_key,
                channel,
                actor,
                text,
                attachment_type,
                attachment_name,
                attachment_kind,
                visible_to_ai,
                forum_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_key,
                channel,
                actor,
                text,
                attachment_type,
                attachment_name,
                attachment_kind,
                int(visible_to_ai),
                forum_status,
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def get_events_for_ai(conversation_key: str) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT *
            FROM conversation_events
            WHERE conversation_key = ?
              AND visible_to_ai = 1
              AND actor IN ('user', 'assistant', 'operator')
            ORDER BY id ASC
            """,
            (conversation_key,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_last_ai_visible_actor(conversation_key: str) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT actor
            FROM conversation_events
            WHERE conversation_key = ?
              AND visible_to_ai = 1
              AND actor IN ('user', 'assistant', 'operator')
            ORDER BY id DESC
            LIMIT 1
            """,
            (conversation_key,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def load_pending_user_tail(conversation_key: str) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT *
            FROM conversation_events
            WHERE conversation_key = ?
              AND actor = 'user'
              AND visible_to_ai = 1
              AND batch_id IS NULL
            ORDER BY id ASC
            """,
            (conversation_key,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def set_batch_id_for_events(event_ids: List[int], batch_id: int) -> None:
    if not event_ids:
        return
    placeholders = ",".join("?" for _ in event_ids)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE conversation_events SET batch_id = ? WHERE id IN ({placeholders})",
            [batch_id, *event_ids],
        )
        await db.commit()


async def mark_pending_user_tail_consumed(conversation_key: str, batch_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE conversation_events
            SET batch_id = ?
            WHERE conversation_key = ?
              AND actor = 'user'
              AND visible_to_ai = 1
              AND batch_id IS NULL
            """,
            (batch_id, conversation_key),
        )
        await db.commit()


async def mark_forum_sent(event_id: int, forum_message_id: Optional[int] = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE conversation_events
            SET forum_status = 'sent',
                forum_message_id = ?
            WHERE id = ?
            """,
            (forum_message_id, event_id),
        )
        await db.commit()


async def mark_forum_failed(event_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE conversation_events SET forum_status = 'failed' WHERE id = ?",
            (event_id,),
        )
        await db.commit()


async def get_topic_for_conversation(conversation_key: str) -> Optional[int]:
    """Return forum topic id for a conversation.
    For telegram: users.topic_id.
    For widget: prefer widget_sessions.topic_id; if absent, lookup forum_topics by
    client_key (tg:<user_id> or site:<site_user_id>) to support persistent-topic scheme.
    """
    channel, subject = parse_conversation_key(conversation_key)
    async with aiosqlite.connect(DB_PATH) as db:
        if channel == "tg":
            cursor = await db.execute(
                "SELECT topic_id FROM users WHERE user_id = ?",
                (int(subject),),
            )
            row = await cursor.fetchone()
            return row[0] if row and row[0] else None

        # widget channel
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT topic_id, user_id, site_user_id FROM widget_sessions WHERE session_id = ?",
            (subject,),
        )
        row = await cursor.fetchone()
        if row and row["topic_id"]:
            return row["topic_id"]

        # Fallback to persistent mapping
        client_key = None
        if row and row["user_id"]:
            client_key = f"tg:{int(row['user_id'])}"
        elif row and row["site_user_id"]:
            client_key = f"site:{row['site_user_id']}"
        if client_key:
            cur2 = await db.execute(
                "SELECT topic_id FROM forum_topics WHERE client_key = ?",
                (client_key,),
            )
            r2 = await cur2.fetchone()
            return r2[0] if r2 else None
        return None


async def load_ai_state(conversation_key: str) -> Dict[str, Any]:
    channel, subject = parse_conversation_key(conversation_key)
    table = "users" if channel == "tg" else "widget_sessions"
    key_field = "user_id" if channel == "tg" else "session_id"
    subject_value: Any = int(subject) if channel == "tg" else subject
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"SELECT ai_paused_until, ai_generation FROM {table} WHERE {key_field} = ?",
            (subject_value,),
        )
        row = await cursor.fetchone()
        if not row:
            return {"ai_paused_until": None, "ai_generation": 0}
        return dict(row)


async def set_ai_state(
    conversation_key: str,
    *,
    ai_paused_until: Optional[str] = None,
    ai_generation: Optional[int] = None,
) -> None:
    channel, subject = parse_conversation_key(conversation_key)
    table = "users" if channel == "tg" else "widget_sessions"
    key_field = "user_id" if channel == "tg" else "session_id"
    subject_value: Any = int(subject) if channel == "tg" else subject

    updates = []
    values: List[Any] = []
    if ai_paused_until is not None:
        updates.append("ai_paused_until = ?")
        values.append(ai_paused_until)
    if ai_generation is not None:
        updates.append("ai_generation = ?")
        values.append(ai_generation)
    if not updates:
        return

    values.append(subject_value)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE {table} SET {', '.join(updates)} WHERE {key_field} = ?",
            values,
        )
        await db.commit()


async def increment_ai_generation(conversation_key: str) -> int:
    state = await load_ai_state(conversation_key)
    new_generation = int(state.get("ai_generation") or 0) + 1
    await set_ai_state(conversation_key, ai_generation=new_generation)
    return new_generation


async def get_latest_user_event_time(conversation_key: str) -> Optional[datetime]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT created_at
            FROM conversation_events
            WHERE conversation_key = ? AND actor = 'user'
            ORDER BY id DESC
            LIMIT 1
            """,
            (conversation_key,),
        )
        row = await cursor.fetchone()
        if not row or not row[0]:
            return None
        return datetime.fromisoformat(str(row[0]))


async def clear_conversation_events(conversation_key: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM conversation_events WHERE conversation_key = ?",
            (conversation_key,),
        )
        await db.commit()
