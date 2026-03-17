# handlers/widget.py
"""
Обработчик сообщений из виджета → отправка в форум Telegram
"""
import logging
import os
import base64
from datetime import datetime
import aiohttp
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BufferedInputFile
from config import SUPPORT_GROUP_ID, ADMINS
from services.widget_session import (
    get_session,
    update_session_topic,
    save_widget_message,
    get_session_messages
)
from services.forum import get_or_create_topic, _topic_exists, send_message_with_retry
from storages.db import get_user, update_user, get_persistent_topic, set_persistent_topic
from keyboards.reply import get_main_menu
from services.localization import load_button, load_text

logger = logging.getLogger(__name__)


async def process_widget_message_to_forum(
    bot: Bot,
    session_id: str,
    user_message: str,
    user_id: int = None,
    username: str = "",
    full_name: str = "",
    image_bytes: bytes = None,
    filename: str = "",
    attachment_type: str = None
):
    """
    Обрабатывает сообщение из виджета и отправляет в форум Telegram
    
    Args:
        bot: Bot instance
        session_id: ID сессии виджета
        user_message: Текст сообщения
        user_id: Telegram user_id (если привязан)
        username: Username из виджета
        full_name: Полное имя из виджета
        image_bytes: Байты изображения (если есть)
        filename: Имя файла
    """
    try:
        # Получаем сессию
        session = await get_session(session_id)
        if not session:
            logger.error(f"❌ Сессия не найдена: {session_id}")
            return None
        
        if session.get("is_blocked"):
            logger.warning(f"🚫 Сессия заблокирована: {session_id}")
            return None
        
        # Определяем пользователя
        telegram_user_id = session.get("user_id") or user_id
        site_user_id = session.get("site_user_id")
        telegram_username = session.get("username") or username or "widget_user"
        telegram_full_name = session.get("full_name") or full_name or "Widget User"
        theme = session.get("theme") or "other"
        topic_id = session.get("topic_id")
        
        # Если есть telegram_user_id — получаем данные из БД
        user = None
        if telegram_user_id:
            user = await get_user(telegram_user_id)
            if user:
                telegram_username = user.get("username") or telegram_username
                telegram_full_name = user.get("full_name") or telegram_full_name
        
        # 🔑 Создаём объект сообщения (эмуляция aiogram Message)
        class MockMessage:
            def __init__(self, text, photo=None, document=None, caption="", from_user=None, chat=None, message_thread_id=None):
                self.text = text
                self.photo = photo or []
                self.document = document
                self.caption = caption
                self.date = datetime.now()
                self.from_user = from_user
                self.chat = chat
                self.message_thread_id = message_thread_id
                # Добавляем остальные атрибуты для совместимости
                self.video = None
                self.audio = None
                self.voice = None
                self.animation = None
                self.sticker = None
                self.contact = None
                self.location = None
                self.venue = None
                self.poll = None
                self.dice = None
                self.new_chat_members = None
                self.left_chat_member = None
                self.migrate_from_chat_id = None
                self.migrate_to_chat_id = None
                self.pinned_message = None
                self.invoice = None
                self.successful_payment = None
                self.passport_data = None
                self.reply_markup = None
                self.media_group_id = None
                self.connected_website = None
                self.forward_from = None
                self.forward_from_chat = None
                self.forward_from_message_id = None
                self.forward_signature = None
                self.forward_sender_name = None
                self.forward_date = None
                self.reply_to_message = None
                self.via_bot = None
                self.edit_date = None
                self.sender_chat = None
                self.author_signature = None
                self.entities = None
                self.caption_entities = []
                self.is_automatic_forward = False
                self.has_protected_content = False
                self.is_topic_message = False
                self.message_id = 0
                self.chat_id = SUPPORT_GROUP_ID
        
        # 🔑 Создаём mock объекты для пользователя и чата
        class MockUser:
            def __init__(self, user_id, username, full_name):
                self.id = user_id
                self.username = username
                self.full_name = full_name
                self.first_name = full_name.split()[0] if full_name else username
                self.last_name = ' '.join(full_name.split()[1:]) if full_name and len(full_name.split()) > 1 else ''
                self.is_bot = False
                self.language_code = 'ru'
                self.can_join_groups = True
                self.can_read_all_group_messages = False
                self.supports_inline_queries = False
        
        class MockChat:
            def __init__(self, chat_id, username, title):
                self.id = chat_id
                self.username = username
                self.title = title
                self.type = 'supergroup'
                self.is_forum = True
        
        mock_from_user = MockUser(telegram_user_id or 0, telegram_username, telegram_full_name)
        mock_chat = MockChat(SUPPORT_GROUP_ID, telegram_username, f"Support Chat {telegram_username}")
        
        # Если есть изображение — создаём mock с фото
        mock_message = None
        if image_bytes:
            mock_message = MockMessage(
                text=user_message,
                from_user=mock_from_user,
                chat=mock_chat
            )
        else:
            mock_message = MockMessage(
                text=user_message,
                from_user=mock_from_user,
                chat=mock_chat
            )
        
        # 🔑 ПРОВЕРКА: есть ли уже topic_id в сессии виджета
        # Логика постоянного топика: ключ либо tg:<id>, либо site:<id>
        client_key = f"tg:{telegram_user_id}" if telegram_user_id else f"site:{site_user_id or 'unknown'}"
        persistent_topic = await get_persistent_topic(client_key)
        existing_topic_id = persistent_topic or session.get("topic_id")
        
        if existing_topic_id:
            # Проверяем существует ли топик
            topic_exists = await _topic_exists(bot, existing_topic_id)
            if topic_exists:
                topic_id = existing_topic_id
                logger.info(f"📍 Используем существующий топик {topic_id} для сессии {session_id[:8]}...")
            else:
                logger.info(f"🕒 Топик {existing_topic_id} не найден — создаём новый")
                topic_id = None
        else:
            topic_id = None
        
        # Если топика нет — создаём новый
        if topic_id is None:
            if telegram_user_id:
                widget_username = f"🌐 {telegram_username}"
                topic_id = await get_or_create_topic(
                    bot=bot,
                    user_id=telegram_user_id,
                    username=widget_username,
                    full_name=telegram_full_name,
                    theme=f"{theme} (widget)",
                    feedback_type=None
                )
            else:
                title_suffix = f"site:{site_user_id}" if site_user_id else "site:unknown"
                title = f"🌐 {title_suffix} | @{telegram_username or '—'}"
                topic = await bot.create_forum_topic(
                    chat_id=SUPPORT_GROUP_ID,
                    name=title[:128],
                    icon_color=0x6FB9F0
                )
                topic_id = topic.message_thread_id

            await update_session_topic(session_id, topic_id, theme)
            # Зафиксировать постоянный топик
            await set_persistent_topic(client_key, topic_id)
            logger.info(f"✅ Создан новый топик {topic_id} для сессии {session_id[:8]}...")
        else:
            logger.info(f"✅ Используем существующий топик {topic_id} для сессии {session_id[:8]}...")
        
        forum_message_id = await _send_widget_message_to_topic(
            bot=bot,
            topic_id=topic_id,
            telegram_user_id=telegram_user_id or 0,
            telegram_username=telegram_username,
            telegram_full_name=telegram_full_name,
            theme=f"{theme} (widget)",
            message=mock_message,
            site_user_id=site_user_id,
            image_bytes=image_bytes,
            image_filename=filename,
            image_mime_type=attachment_type
        )
        
        logger.info(f"✅ Сообщение из виджета отправлено в топик {topic_id}")
        return {"topic_id": topic_id, "forum_message_id": forum_message_id}
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки сообщения виджета: {e}", exc_info=True)
        return None


async def _send_widget_message_to_topic(
    bot: Bot,
    topic_id: int,
    telegram_user_id: int,
    telegram_username: str,
    telegram_full_name: str,
    theme: str,
    message,
    site_user_id: str = None,
    image_bytes: bytes = None,
    image_filename: str = "",
    image_mime_type: str = None
):
    """Отправляет сообщение виджета в уже созданный топик (без создания новых топиков)."""
    id_label = f"tg:{telegram_user_id}" if telegram_user_id else f"site:{site_user_id or 'unknown'}"
    header = (
        f"👤 <b>Пользователь:</b> {telegram_full_name} (@{telegram_username or '—'})\n"
        f"🆔 <b>ID:</b> <code>{id_label}</code>\n"
        f"📌 <b>Тема:</b> {theme}\n"
        f"──────────────────\n"
    )
    text_content = message.text or message.caption or ""

    block_id = site_user_id if site_user_id else str(telegram_user_id)
    block_btn = InlineKeyboardButton(
        text=await load_button("inline", "block"),
        callback_data=f"block_user:{block_id}"
    )
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[[block_btn]])

    if image_bytes:
        caption = (header + text_content).strip()
        if not caption:
            caption = await load_text("photo_from_user")
        input_file = BufferedInputFile(image_bytes, filename=image_filename or "image.jpg")
        if image_mime_type in {"image/jpeg", "image/png"}:
            sent = await bot.send_photo(
                chat_id=SUPPORT_GROUP_ID,
                photo=input_file,
                caption=caption,
                message_thread_id=topic_id,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        else:
            sent = await bot.send_document(
                chat_id=SUPPORT_GROUP_ID,
                document=input_file,
                caption=caption,
                message_thread_id=topic_id,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
    elif getattr(message, "photo", None):
        caption = (header + text_content).strip()
        if not caption:
            caption = await load_text("photo_from_user")
        sent = await bot.send_photo(
            chat_id=SUPPORT_GROUP_ID,
            photo=message.photo[-1].file_id,
            caption=caption,
            message_thread_id=topic_id,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    elif getattr(message, "document", None):
        caption = (header + text_content).strip()
        if not caption:
            caption = await load_text("document_from_user")
        sent = await bot.send_document(
            chat_id=SUPPORT_GROUP_ID,
            document=message.document.file_id,
            caption=caption,
            message_thread_id=topic_id,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    elif getattr(message, "video", None):
        caption = (header + text_content).strip()
        if not caption:
            caption = await load_text("video_from_user")
        sent = await bot.send_video(
            chat_id=SUPPORT_GROUP_ID,
            video=message.video.file_id,
            caption=caption,
            message_thread_id=topic_id,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    else:
        full_text = (header + text_content).strip()
        if not full_text:
            full_text = await load_text("empty_message")
        sent = await send_message_with_retry(
            bot,
            chat_id=SUPPORT_GROUP_ID,
            text=full_text,
            message_thread_id=topic_id,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    return sent.message_id


async def send_operator_reply_to_widget(
    session_id: str,
    operator_message: str,
    operator_name: str = "Оператор",
    attachment_name: str | None = None,
    attachment_type: str | None = None,
    attachment_url: str | None = None,
    attachment_kind: str | None = None
):
    """
    Отправляет ответ оператора из форума в виджет (через WebSocket)
    
    Эта функция вызывается когда оператор отвечает в топике Telegram
    """
    try:
        # Сохраняем сообщение в БД виджета
        message_id = await save_widget_message(
            session_id=session_id,
            text=operator_message,
            sender="operator",
            attachment_type=attachment_type,
            attachment_name=attachment_name
        )

        attachment_payload = None
        if attachment_url and attachment_type:
            attachment_payload = {
                "name": attachment_name or "image",
                "type": attachment_type,
                "url": attachment_url,
                "kind": attachment_kind or ("animation" if attachment_type == "video/mp4" else "image")
            }

        payload = {
            "id": message_id,
            "text": operator_message,
            "sender": "operator",
            "operator_name": operator_name,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "attachment_name": attachment_name,
            "attachment_type": attachment_type,
        }
        if attachment_payload:
            payload["attachment"] = attachment_payload

        await _push_to_widget_api(
            session_id,
            {
                "type": (attachment_kind or ("animation" if attachment_type == "video/mp4" else "image")) if attachment_payload else "message",
                "data": payload
            }
        )
        
        logger.info(f"✅ Ответ оператора отправлен в виджет: {session_id[:8]}...")
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки ответа в виджет: {e}")


async def notify_widget_operator_typing(session_id: str, typing: bool = True):
    """Уведомляет виджет о статусе typing оператора/ИИ."""
    try:
        await _push_to_widget_api(session_id, {
            "type": "typing",
            "data": {
                "typing": typing
            }
        })
    except Exception as e:
        logger.error(f"❌ Ошибка отправки статуса typing в виджет: {e}")


async def _push_to_widget_api(session_id: str, message: dict) -> None:
    url = os.getenv("WIDGET_PUSH_URL", "http://widget-api:8000/api/widget/push")
    payload = {"session_id": session_id, "message": message}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, timeout=5) as resp:
            if resp.status != 200:
                logger.warning(f"⚠️ push to widget failed: {resp.status}")


async def send_operator_image_to_widget(
    session_id: str,
    image_bytes: bytes,
    file_name: str,
    mime_type: str,
    operator_name: str = "Оператор",
    caption: str = ""
):
    attachment_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('utf-8')}"
    await send_operator_reply_to_widget(
        session_id=session_id,
        operator_message=caption,
        operator_name=operator_name,
        attachment_name=file_name,
        attachment_type=mime_type,
        attachment_url=attachment_url,
        attachment_kind="image"
    )
