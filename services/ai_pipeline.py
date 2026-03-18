import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from aiogram import Bot

from config import ADMINS, SUPPORT_GROUP_ID, AI_BATCH_WINDOW_SECONDS, AI_OPERATOR_PAUSE_SECONDS
from services.conversation_store import (
    append_event,
    get_events_for_ai,
    get_topic_for_conversation,
    increment_ai_generation,
    load_ai_state,
    load_pending_user_tail,
    mark_forum_failed,
    mark_forum_sent,
    mark_pending_user_tail_consumed,
    parse_conversation_key,
    set_ai_state,
    set_batch_id_for_events,
)
from services.localization import load_text
from storages.db import get_setting

logger = logging.getLogger(__name__)

_default_bot: Optional[Bot] = None


def _set_default_bot(bot: Bot) -> None:
    global _default_bot
    if _default_bot is None:
        _default_bot = bot

AI_RETRY_DELAYS = (0, 15, 30)
FORUM_RETRY_DELAYS = (30, 120)

debounce_tasks: dict[str, asyncio.Task] = {}
running_ai_tasks: dict[str, asyncio.Task] = {}
pending_media: dict[int, Dict[str, Any]] = {}


def _now() -> datetime:
    return datetime.utcnow()


def _now_iso() -> str:
    return _now().isoformat()


async def _is_ai_enabled() -> bool:
    value = await get_setting("ai_enabled", "1")
    return str(value).strip() not in {"0", "false", "False"}


def _truncate_error(exc: Exception, limit: int = 400) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text[:limit]


def _attachment_kind_from_type(attachment_type: Optional[str]) -> Optional[str]:
    if not attachment_type:
        return None
    if attachment_type == "video/mp4":
        return "animation"
    return "image"


def _history_text_from_event(event: Dict[str, Any]) -> str:
    text = event.get("text") or ""
    actor = event.get("actor")
    attachment_name = event.get("attachment_name")
    attachment_kind = event.get("attachment_kind")
    if actor == "operator":
        marker = "[ОПЕРАТОР]"
        if attachment_kind == "animation" and attachment_name:
            text = f"{text}\n[ОПЕРАТОР_GIF] {attachment_name}".strip()
        elif attachment_name:
            text = f"{text}\n[ОПЕРАТОР_ИЗОБРАЖЕНИЕ] {attachment_name}".strip()
        return f"{marker} {text}".strip()
    if attachment_name and not text.strip():
        if attachment_kind == "animation":
            return f"[GIF] {attachment_name}"
        return f"[ИЗОБРАЖЕНИЕ] {attachment_name}"
    return text


async def _is_paused(conversation_key: str) -> bool:
    state = await load_ai_state(conversation_key)
    paused_until = state.get("ai_paused_until")
    if not paused_until:
        return False
    try:
        return datetime.fromisoformat(str(paused_until)) > _now()
    except ValueError:
        return False


async def _compute_delay(conversation_key: str, delay: int) -> float:
    state = await load_ai_state(conversation_key)
    paused_until = state.get("ai_paused_until")
    if not paused_until:
        return float(delay)
    try:
        remaining = (datetime.fromisoformat(str(paused_until)) - _now()).total_seconds()
    except ValueError:
        remaining = 0
    return float(max(delay, remaining if remaining > 0 else 0))


async def _suppress_if_stale(conversation_key: str, generation: int) -> bool:
    state = await load_ai_state(conversation_key)
    current_generation = int(state.get("ai_generation") or 0)
    if current_generation != generation:
        logger.info(
            "🛑 AI run suppressed by generation change: conversation=%s, expected=%s, actual=%s",
            conversation_key,
            generation,
            current_generation,
        )
        return True
    if await _is_paused(conversation_key):
        logger.info("⏸️ AI run suppressed by pause: conversation=%s", conversation_key)
        return True
    return False


async def pause_conversation_ai(conversation_key: str, seconds: int = AI_OPERATOR_PAUSE_SECONDS) -> int:
    new_generation = await increment_ai_generation(conversation_key)
    await mark_pending_user_tail_consumed(conversation_key, int(time.time() * 1000))
    paused_until = (_now() + timedelta(seconds=seconds)).isoformat()
    await set_ai_state(
        conversation_key,
        ai_paused_until=paused_until,
        ai_generation=new_generation,
    )

    debounce_task = debounce_tasks.pop(conversation_key, None)
    if debounce_task:
        debounce_task.cancel()

    running_task = running_ai_tasks.pop(conversation_key, None)
    if running_task:
        running_task.cancel()

    logger.info(
        "⏸️ Conversation paused: conversation=%s, generation=%s, paused_until=%s",
        conversation_key,
        new_generation,
        paused_until,
    )
    return new_generation


async def schedule_ai_batch(conversation_key: str, delay: int = AI_BATCH_WINDOW_SECONDS) -> None:
    existing = debounce_tasks.pop(conversation_key, None)
    if existing:
        existing.cancel()

    async def _runner() -> None:
        try:
            actual_delay = await _compute_delay(conversation_key, delay)
            await asyncio.sleep(actual_delay)
            await _start_ai_run(conversation_key)
        except asyncio.CancelledError:
            return
        finally:
            if debounce_tasks.get(conversation_key) is task:
                debounce_tasks.pop(conversation_key, None)

    task = asyncio.create_task(_runner())
    debounce_tasks[conversation_key] = task


async def _start_ai_run(conversation_key: str) -> None:
    current = running_ai_tasks.get(conversation_key)
    if current and not current.done():
        logger.info("⏳ AI run already active: conversation=%s", conversation_key)
        return

    task = asyncio.create_task(run_ai_batch(conversation_key))
    running_ai_tasks[conversation_key] = task
    try:
        await task
    finally:
        if running_ai_tasks.get(conversation_key) is task:
            running_ai_tasks.pop(conversation_key, None)


async def _build_ai_history(conversation_key: str, current_event_ids: set[int]) -> list[dict[str, Any]]:
    events = await get_events_for_ai(conversation_key)
    history = []
    for event in events:
        if event["id"] in current_event_ids:
            continue
        history.append(
            {
                "from_user": event["actor"] == "user",
                "text": _history_text_from_event(event),
                "has_media": False,
                "timestamp": str(event["created_at"]),
            }
        )
    return history


async def _send_ai_reply_to_topic(
    bot: Bot,
    *,
    topic_id: int,
    ai_result: Dict[str, Any],
) -> None:
    ai_text = ai_result["response_to_user"].strip()
    if ai_result.get("escalation_reason"):
        ai_text += f"\n\n{await load_text('operator_reason_prefix')} {ai_result['escalation_reason']}"
    if ai_result.get("estimated_time"):
        ai_text += f"\n\n{await load_text('processing_time_prefix')} {ai_result['estimated_time']}"

    await bot.send_message(
        chat_id=SUPPORT_GROUP_ID,
        message_thread_id=topic_id,
        text=f"{await load_text('ai_response_header')}\n{ai_text}",
        parse_mode="HTML",
    )

    if ai_result.get("action") == "escalate":
        admin_tags = " ".join(f"<a href='tg://user?id={admin_id}'>❗</a>" for admin_id in ADMINS)
        reason = ai_result.get("escalation_reason") or "автоматическая эскалация"
        await bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            message_thread_id=topic_id,
            text=f"{admin_tags} <b>{await load_text('operator_notification')}</b>\n{reason}",
            parse_mode="HTML",
        )


async def notify_ai_failure_to_topic(
    bot: Bot,
    *,
    topic_id: Optional[int],
    channel: str,
    conversation_key: str,
    error_text: str,
) -> None:
    if not topic_id:
        logger.warning("⚠️ AI failure without topic: conversation=%s error=%s", conversation_key, error_text)
        return

    admin_tags = " ".join(f"<a href='tg://user?id={admin_id}'>❗</a>" for admin_id in ADMINS)
    text = (
        f"{admin_tags} <b>ИИ недоступен</b>\n\n"
        f"Канал: {channel}\n"
        f"Диалог: <code>{conversation_key}</code>\n\n"
        f"Ошибка:\n<code>{error_text}</code>"
    )
    await bot.send_message(
        chat_id=SUPPORT_GROUP_ID,
        message_thread_id=topic_id,
        text=text,
        parse_mode="HTML",
    )


async def _deliver_assistant_reply(
    bot: Bot,
    *,
    conversation_key: str,
    ai_result: Dict[str, Any],
) -> None:
    channel, subject = parse_conversation_key(conversation_key)

    if channel == "tg":
        from storages.db import get_user, update_user

        user_id = int(subject)
        user = await get_user(user_id)
        response_parts = []
        if user and user.get("first_message_in_ticket") and ai_result.get("estimated_time"):
            response_parts.append(await load_text("first_message_time_info", time=ai_result["estimated_time"]))
        response_parts.append(ai_result["response_to_user"])
        final_response = "\n\n".join(filter(None, response_parts)).strip() or await load_text("fallback_no_response")
        await bot.send_message(user_id, final_response)
        await update_user(user_id, first_message_in_ticket=0)
        await append_event(
            conversation_key=conversation_key,
            channel="telegram",
            actor="assistant",
            text=final_response,
            visible_to_ai=True,
            forum_status="sent",
        )
        topic_id = await get_topic_for_conversation(conversation_key)
        if topic_id:
            await _send_ai_reply_to_topic(bot, topic_id=topic_id, ai_result=ai_result)
        return

    session_id = subject
    from handlers.widget import send_operator_reply_to_widget

    await send_operator_reply_to_widget(
        session_id=session_id,
        operator_message=ai_result["response_to_user"],
        operator_name="Оператор",
    )
    await append_event(
        conversation_key=conversation_key,
        channel="widget",
        actor="assistant",
        text=ai_result["response_to_user"],
        visible_to_ai=True,
        forum_status="sent",
    )
    topic_id = await get_topic_for_conversation(conversation_key)
    if topic_id:
        await _send_ai_reply_to_topic(bot, topic_id=topic_id, ai_result=ai_result)


async def _set_widget_typing(conversation_key: str, typing: bool) -> None:
    channel, subject = parse_conversation_key(conversation_key)
    if channel != "widget":
        return
    from handlers.widget import notify_widget_operator_typing

    await notify_widget_operator_typing(subject, typing=typing)


async def run_ai_batch(conversation_key: str) -> None:
    telegram_bot = _default_bot
    if telegram_bot is None:
        logger.error("❌ No bot instance available for AI batch: %s", conversation_key)
        return

    if not await _is_ai_enabled():
        logger.info("🛑 AI disabled, batch skipped: conversation=%s", conversation_key)
        return

    pending = await load_pending_user_tail(conversation_key)
    if not pending:
        return

    batch_id = int(time.time() * 1000)
    event_ids = [event["id"] for event in pending]
    await set_batch_id_for_events(event_ids, batch_id)

    state = await load_ai_state(conversation_key)
    generation = int(state.get("ai_generation") or 0)
    if await _suppress_if_stale(conversation_key, generation):
        return

    merged_text = "\n".join(
        (_history_text_from_event(event) or "").strip()
        for event in pending
        if (_history_text_from_event(event) or "").strip()
    ).strip()

    image_bytes = None
    filename = ""
    for event in reversed(pending):
        media = pending_media.get(event["id"])
        if media:
            image_bytes = media.get("image_bytes")
            filename = media.get("filename") or ""
            break

    history = await _build_ai_history(conversation_key, set(event_ids))
    channel, subject = parse_conversation_key(conversation_key)
    current_theme = None
    if channel == "tg":
        from storages.db import get_user

        user = await get_user(int(subject))
        current_theme = user.get("theme") if user else None
        user_id = int(subject)
    else:
        from services.widget_session import get_session

        session = await get_session(subject)
        current_theme = session.get("theme") if session else None
        user_id = int(session.get("user_id") or 0) if session else 0

    ai_result = None
    last_error: Optional[Exception] = None
    typing_started = False

    try:
        await _set_widget_typing(conversation_key, True)
        typing_started = True

        for attempt, retry_delay in enumerate(AI_RETRY_DELAYS, start=1):
            if retry_delay:
                await asyncio.sleep(retry_delay)

            if await _suppress_if_stale(conversation_key, generation):
                return

            try:
                from services.ai_agent import process_ticket

                logger.info(
                    "🤖 AI batch start: conversation=%s batch=%s attempt=%s size=%s",
                    conversation_key,
                    batch_id,
                    attempt,
                    len(event_ids),
                )
                ai_result = await process_ticket(
                    user_message=merged_text,
                    history=history,
                    current_theme=current_theme,
                    user_id=user_id,
                    image_bytes=image_bytes,
                    filename=filename,
                )
                break
            except asyncio.CancelledError:
                return
            except Exception as exc:
                last_error = exc
                logger.exception(
                    "💥 AI batch failure: conversation=%s batch=%s attempt=%s",
                    conversation_key,
                    batch_id,
                    attempt,
                )

        if ai_result is None:
            if await _suppress_if_stale(conversation_key, generation):
                return
            error_text = _truncate_error(last_error or RuntimeError("Unknown AI failure"))
            await append_event(
                conversation_key=conversation_key,
                channel="telegram" if channel == "tg" else "widget",
                actor="system",
                text=f"AI failed after 3 retries: {error_text}",
                visible_to_ai=False,
                forum_status="sent",
            )
            topic_id = await get_topic_for_conversation(conversation_key)
            await notify_ai_failure_to_topic(
                telegram_bot,
                topic_id=topic_id,
                channel="telegram" if channel == "tg" else "widget",
                conversation_key=conversation_key,
                error_text=error_text,
            )
            return

        if await _suppress_if_stale(conversation_key, generation):
            return
        try:
            await _deliver_assistant_reply(telegram_bot, conversation_key=conversation_key, ai_result=ai_result)
        except Exception:
            logger.exception("💥 Assistant delivery failed: conversation=%s batch=%s", conversation_key, batch_id)
            raise
    finally:
        for event_id in event_ids:
            pending_media.pop(event_id, None)
        if typing_started:
            try:
                await _set_widget_typing(conversation_key, False)
            except Exception:
                logger.exception("💥 Failed to clear typing state: conversation=%s batch=%s", conversation_key, batch_id)


async def _retry_forum_send(
    send_callable,
    *,
    event_id: int,
    conversation_key: str,
) -> None:
    for index, delay in enumerate(FORUM_RETRY_DELAYS, start=2):
        await asyncio.sleep(delay)
        try:
            result = await send_callable()
            if isinstance(result, dict):
                await mark_forum_sent(event_id, result.get("forum_message_id"))
            else:
                await mark_forum_sent(event_id, result)
            return
        except Exception:
            logger.exception(
                "💥 Forum send failed: conversation=%s event=%s attempt=%s",
                conversation_key,
                event_id,
                index,
            )
    await mark_forum_failed(event_id)


async def handle_incoming_telegram_message(
    *,
    bot: Bot,
    message,
    user: Dict[str, Any],
    theme: str,
    feedback_type: Optional[str],
    user_text: str,
    image_bytes: Optional[bytes] = None,
    filename: str = "",
    attachment_type: Optional[str] = None,
) -> None:
    _set_default_bot(bot)
    conversation_key = f"tg:{user['user_id']}"
    attachment_kind = _attachment_kind_from_type(attachment_type)
    event_id = await append_event(
        conversation_key=conversation_key,
        channel="telegram",
        actor="user",
        text=user_text,
        attachment_type=attachment_type,
        attachment_name=filename or None,
        attachment_kind=attachment_kind,
        visible_to_ai=True,
    )
    if image_bytes:
        pending_media[event_id] = {"image_bytes": image_bytes, "filename": filename}

    async def _send_forum():
        from services.forum import send_to_topic

        forum_message_id = await send_to_topic(bot, user, message, theme, feedback_type)
        return forum_message_id

    try:
        forum_message_id = await _send_forum()
        await mark_forum_sent(event_id, forum_message_id)
    except Exception:
        logger.exception("💥 Initial forum send failed: conversation=%s event=%s", conversation_key, event_id)
        await mark_forum_failed(event_id)
        asyncio.create_task(_retry_forum_send(_send_forum, event_id=event_id, conversation_key=conversation_key))
    await schedule_ai_batch(conversation_key)


async def handle_incoming_widget_message(
    *,
    bot: Bot,
    session_id: str,
    user_message: str,
    image_bytes: Optional[bytes] = None,
    filename: str = "",
    attachment_type: Optional[str] = None,
) -> None:
    _set_default_bot(bot)
    from services.widget_session import get_session

    session = await get_session(session_id)
    if not session:
        logger.warning("⚠️ Widget session missing for pipeline: %s", session_id)
        return

    conversation_key = f"widget:{session_id}"
    attachment_kind = _attachment_kind_from_type(attachment_type)
    event_id = await append_event(
        conversation_key=conversation_key,
        channel="widget",
        actor="user",
        text=user_message,
        attachment_type=attachment_type,
        attachment_name=filename or None,
        attachment_kind=attachment_kind,
        visible_to_ai=True,
    )
    if image_bytes:
        pending_media[event_id] = {"image_bytes": image_bytes, "filename": filename}

    async def _send_forum():
        from handlers.widget import process_widget_message_to_forum

        result = await process_widget_message_to_forum(
            bot=bot,
            session_id=session_id,
            user_message=user_message,
            user_id=session.get("user_id"),
            username=session.get("username") or "widget_user",
            full_name=session.get("full_name") or "Widget User",
            image_bytes=image_bytes,
            filename=filename,
            attachment_type=attachment_type,
        )
        if not result:
            raise RuntimeError("Widget forum delivery returned no result")
        return result

    try:
        result = await _send_forum()
        await mark_forum_sent(event_id, result.get("forum_message_id"))
    except Exception:
        logger.exception("💥 Initial forum send failed: conversation=%s event=%s", conversation_key, event_id)
        await mark_forum_failed(event_id)
        asyncio.create_task(_retry_forum_send(_send_forum, event_id=event_id, conversation_key=conversation_key))
    await schedule_ai_batch(conversation_key)
