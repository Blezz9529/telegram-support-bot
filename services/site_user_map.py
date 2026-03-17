import logging
from datetime import datetime
from typing import Optional

import aiohttp
import aiosqlite

from config import SITE_ID_API_TIMEOUT, SITE_ID_API_TOKEN, SITE_ID_API_URL

DB_PATH = "data/support.db"

logger = logging.getLogger(__name__)


async def init_site_user_map():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS site_user_map (
                site_user_id TEXT PRIMARY KEY,
                telegram_user_id INTEGER,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_site_user_map_telegram
            ON site_user_map(telegram_user_id)
            """
        )
        await db.execute(
            """
            UPDATE site_user_map SET created_at = CURRENT_TIMESTAMP
            WHERE created_at IS NULL
            """
        )
        await db.execute(
            """
            UPDATE site_user_map SET updated_at = CURRENT_TIMESTAMP
            WHERE updated_at IS NULL
            """
        )
        await db.commit()


async def _get_cached_telegram_id(site_user_id: str) -> Optional[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT telegram_user_id FROM site_user_map WHERE site_user_id = ?",
            (site_user_id,),
        )
        row = await cursor.fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0])


async def _get_cached_site_id(telegram_user_id: int) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT site_user_id FROM site_user_map WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        row = await cursor.fetchone()
        if not row or not row[0]:
            return None
        return str(row[0])


async def upsert_site_user_map(site_user_id: str, telegram_user_id: Optional[int]) -> None:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO site_user_map (site_user_id, telegram_user_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(site_user_id) DO UPDATE SET
                telegram_user_id = excluded.telegram_user_id,
                updated_at = excluded.updated_at
            """,
            (site_user_id, telegram_user_id, now, now),
        )
        await db.commit()


async def _fetch_remote_mapping(
    *, site_user_id: Optional[str] = None, telegram_user_id: Optional[int] = None
) -> Optional[dict]:
    if not SITE_ID_API_TOKEN:
        logger.warning("⚠️ SITE_ID_API_TOKEN is empty, falling back to local cache")
        return None

    payload = {}
    if site_user_id:
        payload["id"] = str(site_user_id)
    if telegram_user_id is not None:
        payload["tgid"] = str(telegram_user_id)
    if not payload:
        return None

    headers = {
        "token": SITE_ID_API_TOKEN,
        "Content-Type": "application/json",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=SITE_ID_API_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(SITE_ID_API_URL, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("⚠️ site id api http=%s body=%s", resp.status, body[:300])
                    return None
                data = await resp.json()
    except Exception as exc:
        logger.warning("⚠️ site id api request failed: %s", exc)
        return None

    if not data.get("success"):
        error_code = data.get("error_code")
        if error_code not in {"NO_USER", "NO_PARAM"}:
            logger.warning(
                "⚠️ site id api logical failure: code=%s description=%s",
                error_code,
                data.get("error_description"),
            )
        return None

    resolved_site_id = data.get("id")
    resolved_tg_id = data.get("tgid")
    if not resolved_site_id or not resolved_tg_id:
        logger.warning("⚠️ site id api success without full payload: %s", data)
        return None

    try:
        resolved_tg_id_int = int(resolved_tg_id)
    except (TypeError, ValueError):
        logger.warning("⚠️ site id api returned invalid tgid: %s", resolved_tg_id)
        return None

    await upsert_site_user_map(str(resolved_site_id), resolved_tg_id_int)
    return {
        "site_user_id": str(resolved_site_id),
        "telegram_user_id": resolved_tg_id_int,
    }


async def get_telegram_id_by_site_id(site_user_id: str) -> Optional[int]:
    if not site_user_id:
        return None
    remote = await _fetch_remote_mapping(site_user_id=site_user_id)
    if remote:
        return remote["telegram_user_id"]
    return await _get_cached_telegram_id(site_user_id)


async def get_site_id_by_telegram_id(telegram_user_id: int) -> Optional[str]:
    if telegram_user_id is None:
        return None
    remote = await _fetch_remote_mapping(telegram_user_id=telegram_user_id)
    if remote:
        return remote["site_user_id"]
    return await _get_cached_site_id(telegram_user_id)
