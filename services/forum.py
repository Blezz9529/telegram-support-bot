from aiogram import Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from typing import Optional
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import update_user, get_user
from services.localization import load_text, load_button

async def create_topic(bot: Bot, user_id: int, username: str, full_name: str, theme: str) -> int:
    """Создаёт топик в форуме и возвращает topic_id"""
    title = f"🆔 {user_id} | @{username or '—'}"
    topic = await bot.create_forum_topic(
        chat_id=SUPPORT_GROUP_ID,
        name=title[:128],  # Telegram ограничивает 128 символами
        icon_color=0x6FB9F0  # синий
    )
    await update_user(user_id, topic_id=topic.message_thread_id, theme=theme)
    return topic.message_thread_id

async def get_or_create_topic(bot: Bot, user_id: int, username: str, full_name: str, theme: str) -> int:
    user_data = await get_user(user_id)
    topic_id = user_data.get("topic_id") if user_data else None
    if not topic_id:
        topic_id = await create_topic(bot, user_id, username, full_name, theme)
    return topic_id

async def send_to_topic(
    bot: Bot,
    user: dict,
    message: Message,
    theme: str
) -> Optional[int]:
    """
    Пересылает сообщение в топик.
    Возвращает message_id в топике (для reply-ответов).
    """
    topic_id = await get_or_create_topic(
        bot, user["user_id"], user["username"], user["full_name"], theme
    )

    # Формируем преамбулу
    header = (
        f"👤 <b>Пользователь:</b> {user['full_name']} (@{user['username'] or '—'})\n"
        f"🆔 <b>ID:</b> <code>{user['user_id']}</code>\n"
        f"📌 <b>Тема:</b> {theme}\n"
        f"──────────────────"
    )

    # Отправляем header как отдельное сообщение (не пересылаем, чтобы можно было редактировать/удалить)
    header_msg = await bot.send_message(
        chat_id=SUPPORT_GROUP_ID,
        text=header,
        message_thread_id=topic_id,
        parse_mode="HTML"
    )

    # Пересылаем оригинал
    forwarded = await message.forward(
        chat_id=SUPPORT_GROUP_ID,
        message_thread_id=topic_id
    )

    # Прикрепляем кнопку "Заблокировать"
    block_btn = InlineKeyboardButton(
        text=await load_button("inline", "block"),
        callback_data=f"block_user:{user['user_id']}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[block_btn]])

    await bot.edit_message_reply_markup(
        chat_id=SUPPORT_GROUP_ID,
        message_id=header_msg.message_id,
        reply_markup=kb
    )

    # Сохраняем message_id пересланного сообщения — по нему будем определять reply
    await update_user(user["user_id"], last_message_id=forwarded.message_id)

    # Тэгаем админов, если это первое сообщение
    if user.get("last_message_id") == 0:
        admin_tags = " ".join([f"<a href='tg://user?id={a}'>.</a>" for a in ADMINS])
        await bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            text=admin_tags + "\n" + await load_text("admin_tag"),
            message_thread_id=topic_id,
            parse_mode="HTML"
        )

    return forwarded.message_id