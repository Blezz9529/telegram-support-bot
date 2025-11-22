# services/forum.py
import logging
from aiogram import Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from typing import Optional
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import update_user, get_user
from services.localization import load_text, load_button

logger = logging.getLogger(__name__)


async def _topic_exists(bot: Bot, topic_id: int) -> bool:
    """Проверяет, существует ли топик через get_chat (косвенно)"""
    try:
        # Попытка получить информацию о чате с указанием message_thread_id
        # Даже get_me() с message_thread_id вызовет ошибку, если топик удалён
        await bot.get_chat(SUPPORT_GROUP_ID, message_thread_id=topic_id)
        return True
    except TelegramBadRequest as e:
        if "message thread not found" in str(e).lower():
            return False
        # Другие BadRequest (например, не форум) — считаем, что топик недоступен
        logger.warning(f"TelegramBadRequest при проверке топика {topic_id}: {e}")
        return False
    except TelegramAPIError as e:
        logger.warning(f"Ошибка проверки топика {topic_id}: {e}")
        return False


async def get_or_create_topic(bot: Bot, user_id: int, username: str, full_name: str, theme: str) -> int:
    user_data = await get_user(user_id)
    topic_id = user_data.get("topic_id") if user_data else None

    # Проверяем: есть ли topic_id и жив ли топик
    if topic_id and await _topic_exists(bot, topic_id):
        return topic_id

    # Иначе — создаём новый
    title = f"🆔 {user_id} | @{username or '—'}"
    try:
        topic = await bot.create_forum_topic(
            chat_id=SUPPORT_GROUP_ID,
            name=title[:128],
            icon_color=0x6FB9F0
        )
        new_topic_id = topic.message_thread_id
        await update_user(user_id, topic_id=new_topic_id, theme=theme)
        logger.info(f"✅ Создан топик {new_topic_id} для user_id={user_id}")
        return new_topic_id
    except TelegramAPIError as e:
        logger.error(f"❌ Не удалось создать топик для {user_id}: {e}")
        raise


async def send_to_topic(
    bot: Bot,
    user: dict,
    message: Message,
    theme: str
) -> int:
    topic_id = await get_or_create_topic(
        bot, user["user_id"], user["username"], user["full_name"], theme
    )

    header = (
        f"👤 <b>Пользователь:</b> {user['full_name']} (@{user['username'] or '—'})\n"
        f"🆔 <b>ID:</b> <code>{user['user_id']}</code>\n"
        f"📌 <b>Тема:</b> {theme}\n"
        f"──────────────────"
    )

    header_msg = await bot.send_message(
        chat_id=SUPPORT_GROUP_ID,
        text=header,
        message_thread_id=topic_id,
        parse_mode="HTML"
    )

    forwarded = await message.forward(
        chat_id=SUPPORT_GROUP_ID,
        message_thread_id=topic_id
    )

    block_btn = InlineKeyboardButton(
        text=await load_button("inline", "block"),
        callback_data=f"block_user:{user['user_id']}"
    )
    await bot.edit_message_reply_markup(
        chat_id=SUPPORT_GROUP_ID,
        message_id=header_msg.message_id,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[block_btn]])
    )

    await update_user(user["user_id"], last_message_id=forwarded.message_id)

    # Тэгаем админов при первом сообщении в топике
    if user.get("last_message_id") == 0:
        admin_tags = " ".join([f"<a href='tg://user?id={a}'>.</a>" for a in ADMINS])
        await bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            text=admin_tags + "\n🔔 Новое обращение!",
            message_thread_id=topic_id,
            parse_mode="HTML"
        )

    return forwarded.message_id