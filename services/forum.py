# services/forum.py
import logging
from aiogram import Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
import asyncio
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import update_user, get_user
from services.localization import load_text, load_button

logger = logging.getLogger(__name__)

async def send_message_with_retry(bot, **kwargs):
    for attempt in range(5):
        try:
            return await bot.send_message(**kwargs)
        except TelegramRetryAfter as e:
            delay = e.retry_after + 0.5
            logger.warning(f"⏳ Flood control: ждём {delay:.1f}с")
            await asyncio.sleep(delay)
        except Exception:
            if attempt == 4:
                raise
            await asyncio.sleep(1 * (2 ** attempt))

async def _topic_exists(bot: Bot, topic_id: int) -> bool:
    try:
        msg = await bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            text="🔍",
            message_thread_id=topic_id
        )
        await bot.delete_message(SUPPORT_GROUP_ID, msg.message_id)
        return True
    except (TelegramBadRequest, TelegramAPIError):
        return False

async def get_or_create_topic(bot: Bot, user_id: int, username: str, full_name: str, theme: str) -> int:
    user_data = await get_user(user_id)
    topic_id = user_data.get("topic_id") if user_data else None

    if topic_id and await _topic_exists(bot, topic_id):
        return topic_id

    title = f"🆔 {user_id} | @{username or '—'}"
    try:
        topic = await bot.create_forum_topic(
            chat_id=SUPPORT_GROUP_ID,
            name=title[:128],
            icon_color=0x6FB9F0
        )
        new_id = topic.message_thread_id
        await update_user(user_id, topic_id=new_id, theme=theme)
        logger.info(f"✅ Топик {new_id} для {user_id}")
        return new_id
    except Exception as e:
        logger.error(f"❌ Создание топика: {e}")
        raise

async def send_to_topic(bot: Bot, user: dict, message: Message, theme: str) -> int:
    topic_id = await get_or_create_topic(
        bot, user["user_id"], user["username"], user["full_name"], theme
    )

    header = (
        f"👤 <b>Пользователь:</b> {user['full_name']} (@{user['username'] or '—'})\n"
        f"🆔 <b>ID:</b> <code>{user['user_id']}</code>\n"
        f"📌 <b>Тема:</b> {theme}\n"
        f"──────────────────\n"
    )
    text = message.text or message.caption or ""

    if message.photo:
        await bot.send_photo(
            chat_id=SUPPORT_GROUP_ID,
            photo=message.photo[-1].file_id,
            caption=header + text,
            message_thread_id=topic_id,
            parse_mode="HTML"
        )
        block_btn = InlineKeyboardButton(
            text=await load_button("inline", "block"),
            callback_data=f"block_user:{user['user_id']}"
        )
        await send_message_with_retry(
            bot,
            chat_id=SUPPORT_GROUP_ID,
            text=" ",
            message_thread_id=topic_id,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[block_btn]])
        )
        return message.message_id

    elif message.document:
        await bot.send_document(
            chat_id=SUPPORT_GROUP_ID,
            document=message.document.file_id,
            caption=header + text,
            message_thread_id=topic_id,
            parse_mode="HTML"
        )
        block_btn = InlineKeyboardButton(
            text=await load_button("inline", "block"),
            callback_data=f"block_user:{user['user_id']}"
        )
        await send_message_with_retry(
            bot,
            chat_id=SUPPORT_GROUP_ID,
            text=" ",
            message_thread_id=topic_id,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[block_btn]])
        )
        return message.message_id

    else:
        msg = await send_message_with_retry(
            bot,
            chat_id=SUPPORT_GROUP_ID,
            text=header + text,
            message_thread_id=topic_id,
            parse_mode="HTML"
        )
        
        block_btn = InlineKeyboardButton(
            text=await load_button("inline", "block"),
            callback_data=f"block_user:{user['user_id']}"
        )
        await bot.edit_message_reply_markup(
            chat_id=SUPPORT_GROUP_ID,
            message_id=msg.message_id,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[block_btn]])
        )
        
        await update_user(user["user_id"], last_message_id=msg.message_id)
        
        if user.get("last_message_id") == 0:
            admin_tags = " ".join([f"<a href='tg://user?id={a}'>.</a>" for a in ADMINS])
            await send_message_with_retry(
                bot,
                chat_id=SUPPORT_GROUP_ID,
                text=admin_tags + "\n🔔 Новое обращение!",
                message_thread_id=topic_id,
                parse_mode="HTML"
            )

        return msg.message_id