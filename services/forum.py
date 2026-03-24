# services/forum.py
import logging
import asyncio
from aiogram import Bot
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramAPIError,
    TelegramRetryAfter
)
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import update_user, get_user, get_persistent_topic, set_persistent_topic
from services.localization import load_button, load_text

logger = logging.getLogger(__name__)


# === Проверка существования топика ===
async def _topic_exists(bot: Bot, topic_id: int) -> bool:
    try:
        # Проверяем топик через отправку и удаление служебного сообщения
        msg = await bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            text="🔍",
            message_thread_id=topic_id
        )
        await bot.delete_message(SUPPORT_GROUP_ID, msg.message_id)
        return True
    except (TelegramBadRequest, TelegramAPIError):
        return False


# 🔑 ПРОВЕРКА: возраст топика (не старше 24 часов)
async def _topic_is_fresh(bot: Bot, topic_id: int, max_hours: int = 24) -> bool:
    """Проверяет, что топик был создан недавно"""
    try:
        # Получаем информацию о топике через последнее сообщение
        # Telegram не отдаёт created_at напрямую, но можно проверить по сообщениям
        from datetime import datetime, timedelta
        
        # Получаем последние сообщения в топике
        messages = []
        async for msg in bot.get_chat_history(
            chat_id=SUPPORT_GROUP_ID,
            message_thread_id=topic_id,
            limit=1
        ):
            messages.append(msg)
        
        if not messages:
            return False  # Топик пустой — старый
        
        # Проверяем дату последнего сообщения
        last_msg_date = messages[0].date
        now = datetime.now(tz=last_msg_date.tzinfo)
        age = now - last_msg_date
        
        return age.total_seconds() < (max_hours * 3600)
    except Exception as e:
        logger.warning(f"⚠️ Не удалось проверить возраст топика {topic_id}: {e}")
        return False  # Если не смогли проверить — считаем старым


async def get_or_create_topic(
    bot: Bot,
    user_id: int,
    username: str,
    full_name: str,
    theme: str,
    feedback_type: str = None  # 🔑 Тип отзыва: positive/negative
) -> int:
    # Персистентная логика: одна тема на всю жизнь клиента tg:<id>
    client_key = f"tg:{user_id}"
    topic_id = await get_persistent_topic(client_key)
    if topic_id and await _topic_exists(bot, topic_id):
        logger.info(f"📍 Постоянный топик {topic_id} для {user_id}")
        return topic_id

    # Создаём новый топик
    # 🔑 Формируем заголовок с тегом отзыва
    feedback_tag = ""
    if feedback_type == "positive":
        feedback_tag = "🟢 "
    elif feedback_type == "negative":
        feedback_tag = "🔴 "
    
    title = f"{feedback_tag}🆔 {user_id} | @{username or '—'}"
    
    try:
        topic = await bot.create_forum_topic(
            chat_id=SUPPORT_GROUP_ID,
            name=title[:128],
            icon_color=0x6FB9F0
        )
        new_id = topic.message_thread_id
        await update_user(user_id, topic_id=new_id, theme=theme, feedback_type=feedback_type)
        await set_persistent_topic(client_key, new_id)
        logger.info(f"✅ Топик {new_id} для {user_id} (тип: {feedback_type or 'обычный'})")
        return new_id
    except TelegramAPIError as e:
        logger.error(f"❌ Не удалось создать топик: {e}")
        raise


# === Надёжная отправка сообщения ===
async def send_message_with_retry(bot: Bot, **kwargs):
    for attempt in range(3):
        try:
            return await bot.send_message(**kwargs)
        except TelegramRetryAfter as e:
            delay = e.retry_after + 0.5
            logger.warning(f"⏳ Flood control: ждём {delay:.1f}с")
            await asyncio.sleep(delay)
        except TelegramBadRequest as e:
            if "text must be non-empty" in str(e):
                if "text" in kwargs and not kwargs["text"].strip():
                    kwargs["text"] = "—"
                else:
                    raise
            else:
                raise
        except Exception as e:
            if attempt == 2:
                raise
            delay = 1 * (2 ** attempt)
            logger.warning(f"⚠️ Ошибка отправки: {e}. Повтор через {delay}s")
            await asyncio.sleep(delay)


async def send_to_topic(
    bot: Bot,
    user: dict,
    message: Message,
    theme: str,
    feedback_type: str = None  # 🔑 Тип отзыва: positive/negative
) -> int:
    topic_id = await get_or_create_topic(
        bot, user["user_id"], user["username"], user["full_name"], theme, feedback_type
    )

    # 🔑 Добавляем тег отзыва в заголовок сообщения
    feedback_tag = ""
    if feedback_type == "positive":
        feedback_tag = "🟢 <b>Положительный отзыв</b>\n"
    elif feedback_type == "negative":
        feedback_tag = "🔴 <b>Отрицательный отзыв</b>\n"

    header = (
        f"👤 <b>Пользователь:</b> {user['full_name']} (@{user['username'] or '—'})\n"
        f"🆔 <b>ID:</b> <code>{user['user_id']}</code>\n"
        f"{feedback_tag}"
        f"📌 <b>Тема:</b> {theme}\n"
        f"──────────────────\n"
    )
    text_content = message.text or message.caption or ""

    # Кнопка блокировки
    block_btn = InlineKeyboardButton(
        text=await load_button("inline", "block"),
        callback_data=f"block_user:{user['user_id']}"
    )
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[[block_btn]])

    # Текстовые сообщения — как раньше
    if message.content_type == "text":
        full_text = (header + text_content).strip()
        if not full_text:
            full_text = await load_text("empty_message")
        sent = await send_message_with_retry(
            bot,
            chat_id=SUPPORT_GROUP_ID,
            text=full_text,
            message_thread_id=topic_id,
            parse_mode="HTML",
            reply_markup=reply_markup  # ✅ Кнопка в текстовом сообщении
        )
    else:
        # Любые вложения — сначала заголовок, затем копия оригинального сообщения
        await send_message_with_retry(
            bot,
            chat_id=SUPPORT_GROUP_ID,
            text=header.strip(),
            message_thread_id=topic_id,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        try:
            sent = await bot.copy_message(
                chat_id=SUPPORT_GROUP_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=topic_id,
            )
        except Exception:
            placeholder = (header + (text_content or f"[{message.content_type}]")).strip()
            sent = await send_message_with_retry(
                bot,
                chat_id=SUPPORT_GROUP_ID,
                text=placeholder,
                message_thread_id=topic_id,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )

    # ✅ СОХРАНЯЕМ message_id для отслеживания (но НЕ тэг админов при создании)
    await update_user(user["user_id"], last_message_id=sent.message_id)
    logger.info(f"✅ Сообщение {sent.message_id} отправлено в топик {topic_id} для {user['user_id']}")
    return sent.message_id
