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


async def get_or_create_topic(bot: Bot, user_id: int, username: str, full_name: str, theme: str) -> int:
    """
    Возвращает topic_id.
    - Берёт из БД, если есть.
    - Проверяет живость через попытку отправить служебное сообщение.
    - При ошибке — создаёт новый, обновляет БД.
    """
    user_data = await get_user(user_id)
    topic_id = user_data.get("topic_id") if user_data else None

    # Попробуем использовать сохранённый topic_id
    if topic_id:
        try:
            # Отправляем "пинг" в топик (удаляем сразу)
            msg = await bot.send_message(
                chat_id=SUPPORT_GROUP_ID,
                text="🔍 Проверка топика (удалится)",
                message_thread_id=topic_id
            )
            await bot.delete_message(SUPPORT_GROUP_ID, msg.message_id)
            return topic_id  # топик жив
        except (TelegramBadRequest, TelegramAPIError) as e:
            if "message thread not found" in str(e).lower() or "topic deleted" in str(e).lower():
                logger.warning(f"Топик {topic_id} для user_id={user_id} удалён. Создаём новый.")
            else:
                logger.warning(f"Ошибка при проверке топика {topic_id}: {e}")
            # Сбрасываем topic_id, чтобы создать новый
            topic_id = None

    # Создаём новый топик
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
    """
    Пересылает сообщение в топик. Если топик удалён — создаёт новый и повторяет.
    """
    for attempt in range(2):  # максимум 2 попытки
        topic_id = await get_or_create_topic(
            bot, user["user_id"], user["username"], user["full_name"], theme
        )

        try:
            # Преамбула
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

            # Пересылаем сообщение
            forwarded = await message.forward(
                chat_id=SUPPORT_GROUP_ID,
                message_thread_id=topic_id
            )

            # Кнопка блокировки
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

            # Тэгаем админов при первом сообщении
            if user.get("last_message_id") == 0:
                admin_tags = " ".join([f"<a href='tg://user?id={a}'>.</a>" for a in ADMINS])
                await bot.send_message(
                    chat_id=SUPPORT_GROUP_ID,
                    text=admin_tags + "\n🔔 Новое обращение!",
                    message_thread_id=topic_id,
                    parse_mode="HTML"
                )

            return forwarded.message_id

        except (TelegramBadRequest, TelegramAPIError) as e:
            if "message thread not found" in str(e).lower() and attempt == 0:
                # Удаляем старый topic_id из БД и повторяем
                await update_user(user["user_id"], topic_id=None)
                logger.warning(f"Топик {topic_id} удалён. Повторная попытка...")
                continue
            else:
                raise

    raise RuntimeError("Не удалось отправить сообщение в топик после 2 попыток")