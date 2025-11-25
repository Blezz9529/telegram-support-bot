# services/forum.py
import logging
import asyncio
from typing import Optional
from aiogram import Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramRetryAfter
)
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import update_user, get_user
from services.localization import load_text, load_button

logger = logging.getLogger(__name__)


async def send_message_with_retry(bot: Bot, **kwargs):
    """Надёжная отправка сообщения с retry при Flood Control"""
    for attempt in range(5):
        try:
            return await bot.send_message(**kwargs)
        except TelegramRetryAfter as e:
            delay = e.retry_after + 0.5
            logger.warning(f"⏳ Flood control: ждём {delay:.1f}с")
            await asyncio.sleep(delay)
        except TelegramBadRequest as e:
            if "text must be non-empty" in str(e):
                # Защита от пустого text — добавляем минимальный контент
                if "text" in kwargs and not kwargs["text"].strip():
                    kwargs["text"] = "—"
                else:
                    raise
            else:
                raise
        except Exception as e:
            if attempt == 4:
                raise
            await asyncio.sleep(1 * (2 ** attempt))


async def _topic_exists(bot: Bot, topic_id: int) -> bool:
    """Проверяет, существует ли топик через отправку и удаление служебного сообщения"""
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
    """Возвращает topic_id. Создаёт новый, если текущий удалён или отсутствует."""
    user_data = await get_user(user_id)
    topic_id = user_data.get("topic_id") if user_data else None

    # Проверяем существование топика
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
    except Exception as e:
        logger.error(f"❌ Не удалось создать топик: {e}")
        raise


async def send_to_topic(
    bot: Bot,
    user: dict,
    message: Message,
    theme: str
) -> int:
    """
    Пересылает сообщение пользователя в топик.
    Поддерживает: текст, фото, документы, видео.
    Прикрепляет кнопку «Заблокировать» к сообщению.
    """
    topic_id = await get_or_create_topic(
        bot, user["user_id"], user["username"], user["full_name"], theme
    )

    # Преамбула с данными пользователя
    header = (
        f"👤 <b>Пользователь:</b> {user['full_name']} (@{user['username'] or '—'})\n"
        f"🆔 <b>ID:</b> <code>{user['user_id']}</code>\n"
        f"📌 <b>Тема:</b> {theme}\n"
        f"──────────────────\n"
    )
    text_content = message.text or message.caption or ""

    # Формируем полный caption (для медиа) или текст (для текстовых сообщений)
    full_caption = (header + text_content).strip()
    if not full_caption:
        full_caption = "📎 Сообщение от пользователя"

    # Создаём кнопку блокировки
    block_btn = InlineKeyboardButton(
        text=await load_button("inline", "block"),
        callback_data=f"block_user:{user['user_id']}"
    )
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[[block_btn]])

    # Отправляем в зависимости от типа сообщения
    if message.photo:
        sent = await bot.send_photo(
            chat_id=SUPPORT_GROUP_ID,
            photo=message.photo[-1].file_id,
            caption=full_caption,
            message_thread_id=topic_id,
            parse_mode="HTML",
            reply_markup=reply_markup  # ✅ Кнопка прямо в фото
        )

    elif message.document:
        sent = await bot.send_document(
            chat_id=SUPPORT_GROUP_ID,
            document=message.document.file_id,
            caption=full_caption,
            message_thread_id=topic_id,
            parse_mode="HTML",
            reply_markup=reply_markup  # ✅ Кнопка прямо в документе
        )

    elif message.video:
        sent = await bot.send_video(
            chat_id=SUPPORT_GROUP_ID,
            video=message.video.file_id,
            caption=full_caption,
            message_thread_id=topic_id,
            parse_mode="HTML",
            reply_markup=reply_markup  # ✅ Кнопка прямо в видео
        )

    else:
        # Текстовое сообщение
        sent = await send_message_with_retry(
            bot,
            chat_id=SUPPORT_GROUP_ID,
            text=full_caption,
            message_thread_id=topic_id,
            parse_mode="HTML",
            reply_markup=reply_markup
        )

    # Сохраняем message_id для reply-трекинга
    await update_user(user["user_id"], last_message_id=sent.message_id)

    # Тэгаем админов при первом сообщении в топике
    if user.get("last_message_id") == 0:
        admin_tags = " ".join([f"<a href='tg://user?id={a}'>.</a>" for a in ADMINS])
        await send_message_with_retry(
            bot,
            chat_id=SUPPORT_GROUP_ID,
            text=admin_tags + "\n🔔 Новое обращение!",
            message_thread_id=topic_id,
            parse_mode="HTML"
        )

    return sent.message_id