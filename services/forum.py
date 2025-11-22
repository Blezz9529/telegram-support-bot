# services/forum.py
import logging
from aiogram import Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError
from typing import Optional
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import update_user, get_user
from services.localization import load_text, load_button

logger = logging.getLogger(__name__)


async def _topic_exists(bot: Bot, topic_id: int) -> bool:
    """Проверяет, существует ли топик (не удалён)"""
    try:
        await bot.get_forum_topic(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id)
        return True
    except TelegramAPIError:
        return False


async def get_or_create_topic(bot: Bot, user_id: int, username: str, full_name: str, theme: str) -> int:
    """
    Возвращает topic_id для пользователя.
    - Если topic_id есть в БД и топик существует → возвращаем его.
    - Если topic_id есть, но топик удалён → создаём новый, обновляем БД.
    - Если topic_id нет → создаём новый, сохраняем в БД.
    """
    user_data = await get_user(user_id)
    topic_id = user_data.get("topic_id") if user_data else None

    # Случай 1: topic_id есть и топик жив → используем его
    if topic_id and await _topic_exists(bot, topic_id):
        return topic_id

    # Случай 2: topic_id есть, но топик удалён → создаём новый
    if topic_id:
        logger.warning(f"Топик {topic_id} для user_id={user_id} удалён. Создаём новый.")

    # Случай 3: topic_id нет (новый пользователь) → создаём
    title = f"🆔 {user_id} | @{username or '—'}"
    try:
        topic = await bot.create_forum_topic(
            chat_id=SUPPORT_GROUP_ID,
            name=title[:128],
            icon_color=0x6FB9F0  # синий
        )
        new_topic_id = topic.message_thread_id
        # 🔑 КЛЮЧЕВОЙ МОМЕНТ: сохраняем topic_id в БД
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
    """Пересылает сообщение в топик. Возвращает message_id в топике."""
    topic_id = await get_or_create_topic(
        bot, user["user_id"], user["username"], user["full_name"], theme
    )

    # Преамбула с данными пользователя
    header = (
        f"👤 <b>Пользователь:</b> {user['full_name']} (@{user['username'] or '—'})\n"
        f"🆔 <b>ID:</b> <code>{user['user_id']}</code>\n"
        f"📌 <b>Тема:</b> {theme}\n"
        f"──────────────────"
    )

    # Отправляем header
    header_msg = await bot.send_message(
        chat_id=SUPPORT_GROUP_ID,
        text=header,
        message_thread_id=topic_id,
        parse_mode="HTML"
    )

    # Пересылаем сообщение пользователя
    forwarded = await message.forward(
        chat_id=SUPPORT_GROUP_ID,
        message_thread_id=topic_id
    )

    # Кнопка "Заблокировать"
    block_btn = InlineKeyboardButton(
        text=await load_button("inline", "block"),
        callback_data=f"block_user:{user['user_id']}"
    )
    await bot.edit_message_reply_markup(
        chat_id=SUPPORT_GROUP_ID,
        message_id=header_msg.message_id,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[block_btn]])
    )

    # Сохраняем message_id для reply-трекинга
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