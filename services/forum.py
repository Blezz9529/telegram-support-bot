# services/forum.py
import logging
from aiogram import Bot
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import update_user, get_user
from services.localization import load_button

logger = logging.getLogger(__name__)


# ✅ Исправлено: проверка существования топика через попытку отправки
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


async def get_or_create_topic(
    bot: Bot,
    user_id: int,
    username: str,
    full_name: str,
    theme: str
) -> int:
    user_data = await get_user(user_id)
    topic_id = user_data.get("topic_id") if user_data else None

    # Проверяем, существует ли топик
    if topic_id and await _topic_exists(bot, topic_id):
        return topic_id

    # Создаём новый топик
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
    except TelegramAPIError as e:
        logger.error(f"❌ Не удалось создать топик: {e}")
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
        f"──────────────────\n"
    )
    text_content = message.text or message.caption or ""

    block_btn = InlineKeyboardButton(
        text=await load_button("inline", "block"),
        callback_data=f"block_user:{user['user_id']}"
    )
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[[block_btn]])

    if message.photo:
        caption = (header + text_content).strip()
        if not caption:
            caption = "🖼️ Фото от пользователя"
        sent = await bot.send_photo(
            chat_id=SUPPORT_GROUP_ID,
            photo=message.photo[-1].file_id,
            caption=caption,
            message_thread_id=topic_id,
            parse_mode="HTML",
            reply_markup=reply_markup  # ✅ Кнопка сразу в фото
        )
    elif message.document:
        caption = (header + text_content).strip()
        if not caption:
            caption = "📎 Документ от пользователя"
        sent = await bot.send_document(
            chat_id=SUPPORT_GROUP_ID,
            document=message.document.file_id,
            caption=caption,
            message_thread_id=topic_id,
            parse_mode="HTML",
            reply_markup=reply_markup  # ✅ Кнопка сразу в документе
        )
    elif message.video:
        caption = (header + text_content).strip()
        if not caption:
            caption = "🎬 Видео от пользователя"
        sent = await bot.send_video(
            chat_id=SUPPORT_GROUP_ID,
            video=message.video.file_id,
            caption=caption,
            message_thread_id=topic_id,
            parse_mode="HTML",
            reply_markup=reply_markup  # ✅ Кнопка сразу в видео
        )
    else:
        full_text = (header + text_content).strip()
        if not full_text:
            full_text = "💬 Пустое сообщение от пользователя"
        sent = await bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            text=full_text,
            message_thread_id=topic_id,
            parse_mode="HTML",
            reply_markup=reply_markup  # ✅ Кнопка в текстовом сообщении
        )

    await update_user(user["user_id"], last_message_id=sent.message_id)
    return sent.message_id