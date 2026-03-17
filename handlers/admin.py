# handlers/admin.py
from aiogram import Router, Bot, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import get_user, get_user_by_topic_id, update_user, backup_database, restore_database, get_db_stats, get_backup_list
from services.localization import load_text
from services.widget_session import get_session_by_user_id, get_session_by_topic_id, get_latest_session_by_site_user_id, block_sessions_by_site_user_id, unblock_sessions_by_site_user_id
from handlers.widget import send_operator_reply_to_widget
from services.site_user_map import get_telegram_id_by_site_id, get_site_id_by_telegram_id
from services.conversation_store import append_event
from services.ai_pipeline import pause_conversation_ai
import logging
import os
import base64
from datetime import datetime

logger = logging.getLogger(__name__)

router = Router()


async def _download_tg_file_bytes(bot: Bot, file_id: str) -> bytes:
    file = await bot.get_file(file_id)
    payload = await bot.download_file(file.file_path)
    if hasattr(payload, "getvalue"):
        return payload.getvalue()
    return payload


def _to_data_url(file_bytes: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(file_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"

# Фильтр: только сообщения из SUPPORT_GROUP_ID, в топиках, и с reply
@router.message(
    F.chat.id == SUPPORT_GROUP_ID,
    F.message_thread_id,
    F.reply_to_message
)
async def handle_admin_reply(message: Message, bot: Bot):
    """Обработка ответов админов в топиках"""
    reply = message.reply_to_message
    topic_id = message.message_thread_id

    user = None
    user_id = None
    widget_session = None

    if reply and reply.forward_from:
        user_id = reply.forward_from.id
        user = await get_user(user_id)
        logger.info(f"🧩 reply.forward_from user_id={user_id}")

    if not user and topic_id:
        user = await get_user_by_topic_id(topic_id)
        if user:
            user_id = user["user_id"]
            logger.info(f"🧩 user найден по topic_id={topic_id}: user_id={user_id}")

    if not user and topic_id:
        widget_session = await get_session_by_topic_id(topic_id)
        if widget_session:
            logger.info(
                f"🧩 widget session найден по topic_id={topic_id}: "
                f"session_id={widget_session['session_id'][:8]}..., "
                f"site_user_id={widget_session.get('site_user_id')}, "
                f"user_id={widget_session.get('user_id')}"
            )

    if not user and not widget_session and reply:
        content = reply.text or reply.caption or ""
        import re
        match = re.search(r"site:([A-Za-z0-9_\\-]+)", content)
        if match:
            site_user_id = match.group(1)
            widget_session = await get_latest_session_by_site_user_id(site_user_id)
            if widget_session:
                logger.info(
                    f"🧩 widget session найден по site_user_id из текста: "
                    f"site_user_id={site_user_id}, session_id={widget_session['session_id'][:8]}..."
                )

    if not user and not widget_session:
        await message.reply(await load_text("invalid_reply"))
        return

    if user and user["is_blocked"]:
        await message.reply(f"❌ Пользователь {user_id} заблокирован — ответ не отправлен.")
        return

    # Отправляем ответ пользователю
    try:
        unsupported_widget_text = "Оператор отправил файл. Этот формат пока не поддерживается в виджете."
        operator_message = message.text or message.caption or ""
        widget_history_text = operator_message
        tg_attachment_type = None
        tg_attachment_name = None
        tg_attachment_kind = None

        if user_id:
            if message.text:
                await bot.send_message(user_id, f"💬 <b>Ответ оператора:</b>\n{message.text}", parse_mode="HTML")
            elif message.photo:
                tg_attachment_type = "image/jpeg"
                tg_attachment_name = f"photo_{message.photo[-1].file_unique_id}.jpg"
                tg_attachment_kind = "image"
                await bot.send_photo(
                    user_id,
                    photo=message.photo[-1].file_id,
                    caption=message.caption or "",
                    parse_mode="HTML"
                )
            elif message.document:
                tg_attachment_type = message.document.mime_type or "application/octet-stream"
                tg_attachment_name = message.document.file_name or "document"
                await bot.send_document(
                    user_id,
                    document=message.document.file_id,
                    caption=message.caption or "",
                    parse_mode="HTML"
                )
            elif message.video:
                tg_attachment_type = "video/mp4"
                tg_attachment_name = f"video_{message.video.file_unique_id}.mp4"
                await bot.send_video(
                    user_id,
                    video=message.video.file_id,
                    caption=message.caption or "",
                    parse_mode="HTML"
                )
            elif message.animation:
                tg_attachment_type = message.animation.mime_type or "video/mp4"
                tg_attachment_name = message.animation.file_name or f"animation_{message.animation.file_unique_id}.mp4"
                tg_attachment_kind = "animation"
                await bot.send_animation(
                    user_id,
                    animation=message.animation.file_id,
                    caption=message.caption or "",
                    parse_mode="HTML"
                )
            elif message.sticker:
                tg_attachment_type = "image/webp"
                tg_attachment_name = f"sticker_{message.sticker.file_unique_id}.webp"
                tg_attachment_kind = "image"
                await bot.send_sticker(user_id, sticker=message.sticker.file_id)
            else:
                await message.reply("⚠️ Поддерживаются только текст, фото, документы, видео, GIF и стикеры.")
                return

        await message.reply(await load_text("reply_sent_to_user"))
        
        # 🔑 ОТПРАВЛЯЕМ ОТВЕТ В ВИДЖЕТ (если пользователь там)
        try:
            if not widget_session and user_id:
                widget_session = await get_session_by_user_id(user_id)

            if widget_session and widget_session.get("site_user_id"):
                latest = await get_latest_session_by_site_user_id(widget_session["site_user_id"])
                if latest:
                    widget_session = latest

            if widget_session:
                if message.photo:
                    image_bytes = await _download_tg_file_bytes(bot, message.photo[-1].file_id)
                    await send_operator_reply_to_widget(
                        session_id=widget_session["session_id"],
                        operator_message=operator_message,
                        operator_name="Оператор",
                        attachment_name=f"photo_{message.photo[-1].file_unique_id}.jpg",
                        attachment_type="image/jpeg",
                        attachment_url=_to_data_url(image_bytes, "image/jpeg"),
                        attachment_kind="image"
                    )
                elif message.animation:
                    mime_type = message.animation.mime_type or "video/mp4"
                    if mime_type == "video/mp4":
                        animation_bytes = await _download_tg_file_bytes(bot, message.animation.file_id)
                        await send_operator_reply_to_widget(
                            session_id=widget_session["session_id"],
                            operator_message=operator_message,
                            operator_name="Оператор",
                            attachment_name=message.animation.file_name or f"animation_{message.animation.file_unique_id}.mp4",
                            attachment_type=mime_type,
                            attachment_url=_to_data_url(animation_bytes, mime_type),
                            attachment_kind="animation"
                        )
                    else:
                        widget_history_text = unsupported_widget_text
                        await send_operator_reply_to_widget(
                            session_id=widget_session["session_id"],
                            operator_message=unsupported_widget_text,
                            operator_name="Оператор"
                        )
                elif message.sticker:
                    if message.sticker.is_animated or message.sticker.is_video:
                        widget_history_text = unsupported_widget_text
                        await send_operator_reply_to_widget(
                            session_id=widget_session["session_id"],
                            operator_message=unsupported_widget_text,
                            operator_name="Оператор"
                        )
                    else:
                        sticker_bytes = await _download_tg_file_bytes(bot, message.sticker.file_id)
                        await send_operator_reply_to_widget(
                            session_id=widget_session["session_id"],
                            operator_message=operator_message or (message.sticker.emoji or ""),
                            operator_name="Оператор",
                            attachment_name=f"sticker_{message.sticker.file_unique_id}.webp",
                            attachment_type="image/webp",
                            attachment_url=_to_data_url(sticker_bytes, "image/webp"),
                            attachment_kind="image"
                        )
                elif message.text:
                    await send_operator_reply_to_widget(
                        session_id=widget_session["session_id"],
                        operator_message=operator_message,
                        operator_name="Оператор"
                    )
                else:
                    widget_history_text = unsupported_widget_text
                    await send_operator_reply_to_widget(
                        session_id=widget_session["session_id"],
                        operator_message=unsupported_widget_text,
                        operator_name="Оператор"
                    )
                logger.info(f"📤 Ответ оператора отправлен в виджет: {widget_session['session_id'][:8]}...")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось отправить в виджет: {e}")

        if user_id:
            await append_event(
                conversation_key=f"tg:{user_id}",
                channel="telegram",
                actor="operator",
                text=operator_message,
                attachment_type=tg_attachment_type,
                attachment_name=tg_attachment_name,
                attachment_kind=tg_attachment_kind,
                visible_to_ai=True,
                forum_status="sent",
            )
            await pause_conversation_ai(f"tg:{user_id}")

        if widget_session:
            widget_attachment_type = tg_attachment_type
            widget_attachment_name = tg_attachment_name
            widget_attachment_kind = tg_attachment_kind
            if message.photo:
                widget_attachment_type = "image/jpeg"
                widget_attachment_name = f"photo_{message.photo[-1].file_unique_id}.jpg"
                widget_attachment_kind = "image"
            elif message.animation:
                widget_attachment_type = message.animation.mime_type or "video/mp4"
                widget_attachment_name = message.animation.file_name or f"animation_{message.animation.file_unique_id}.mp4"
                widget_attachment_kind = "animation"
            elif message.sticker and not (message.sticker.is_animated or message.sticker.is_video):
                widget_attachment_type = "image/webp"
                widget_attachment_name = f"sticker_{message.sticker.file_unique_id}.webp"
                widget_attachment_kind = "image"
            await append_event(
                conversation_key=f"widget:{widget_session['session_id']}",
                channel="widget",
                actor="operator",
                text=widget_history_text,
                attachment_type=widget_attachment_type,
                attachment_name=widget_attachment_name,
                attachment_kind=widget_attachment_kind,
                visible_to_ai=True,
                forum_status="sent",
            )
            await pause_conversation_ai(f"widget:{widget_session['session_id']}")
            
    except Exception as e:
        await message.reply(f"❌ Ошибка отправки: {e}")


@router.callback_query(F.data.startswith("block_user:"))
async def block_user(callback: CallbackQuery, bot: Bot):
    raw_id = callback.data.split(":")[1] if ":" in callback.data else ""
    is_int_id = False
    try:
        user_id = int(raw_id)
        is_int_id = True
    except (ValueError, IndexError):
        user_id = None

    user = await get_user(user_id) if is_int_id else None
    if user:
        if user["is_blocked"]:
            await callback.answer(await load_text("already_blocked", user_id=user_id), show_alert=True)
            return

        await update_user(user_id, is_blocked=True)
        try:
            await bot.send_message(user_id, await load_text("blocked"))
        except Exception:
            pass

        await callback.answer(await load_text("user_blocked_success", user_id=user_id), show_alert=True)
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
        logger.info(f"🚫 Заблокирован tg user_id={user_id}")
        return

    site_user_id = raw_id or None
    if not site_user_id:
        await callback.answer("❌ Некорректный ID", show_alert=True)
        return

    blocked_count = await block_sessions_by_site_user_id(site_user_id)
    mapped_user_id = await get_telegram_id_by_site_id(site_user_id)
    if mapped_user_id:
        await update_user(mapped_user_id, is_blocked=True)
        try:
            await bot.send_message(mapped_user_id, await load_text("blocked"))
        except Exception:
            pass
    logger.info(
        f"🚫 Заблокирован site_user_id={site_user_id}, sessions={blocked_count}, "
        f"mapped_telegram={mapped_user_id}"
    )
    await callback.answer(await load_text("user_blocked_success", user_id=site_user_id), show_alert=True)
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)


# 🔑 КОМАНДЫ УПРАВЛЕНИЯ БАЗОЙ ДАННЫХ

@router.message(Command("db_backup"))
async def cmd_db_backup(message: Message):
    """Создать бэкап БД"""
    if message.from_user.id not in ADMINS:
        return
    
    try:
        backup_path = await backup_database()
        await message.answer(f"✅ Бэкап создан:\n<code>{backup_path}</code>")
        
        # Отправляем файл админу
        await bot.send_document(
            message.from_user.id,
            open(backup_path, 'rb'),
            caption=f"Бэкап БД от {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("db_restore"))
async def cmd_db_restore(message: Message):
    """Восстановить БД из бэкапа (по имени файла)"""
    if message.from_user.id not in ADMINS:
        return
    
    # Получаем имя файла из аргументов
    backup_name = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
    
    if not backup_name:
        await message.answer(
            "Использование: /db_restore support_backup_20240101_120000.db\n\n"
            "Доступные бэкапы:\n" + 
            "\n".join([b['name'] for b in await get_backup_list()[:5]])
        )
        return
    
    backup_path = os.path.join("data/backups", backup_name)
    
    result = await restore_database(backup_path)
    if result:
        await message.answer("✅ БД восстановлена! Перезапустите бота.")
    else:
        await message.answer(f"❌ Ошибка восстановления. Проверьте имя файла.")


@router.message(Command("db_stats"))
async def cmd_db_stats(message: Message):
    """Показать статистику БД"""
    if message.from_user.id not in ADMINS:
        return
    
    stats = await get_db_stats()
    await message.answer(
        f"📊 <b>Статистика БД</b>\n\n"
        f"Всего пользователей: {stats['total_users']}\n"
        f"Активные (24ч): {stats['active_users_24h']}\n"
        f"Заблокировано: {stats['blocked_users']}\n"
        f"Размер БД: {stats['db_size_mb']} MB"
    )


@router.message(Command("db_list"))
async def cmd_db_list(message: Message):
    """Показать список бэкапов"""
    if message.from_user.id not in ADMINS:
        return
    
    backups = await get_backup_list()
    
    if not backups:
        await message.answer("📁 Бэкапов нет")
        return
    
    text = "📁 <b>Бэкапы</b> (последние 10):\n\n"
    for i, b in enumerate(backups[:10], 1):
        text += f"{i}. <code>{b['name']}</code> — {b['size']} байт, {b['created']}\n"
    
    await message.answer(text)


def _is_admin(message: Message) -> bool:
    return message.from_user and message.from_user.id in ADMINS


@router.message(F.text.regexp(r"^/ban_tg_(\d+)$"))
async def cmd_ban_tg(message: Message):
    if not _is_admin(message):
        return
    user_id = int(message.text.split("_", 2)[2])
    user = await get_user(user_id)
    if not user:
        await message.answer("❌ Пользователь не найден.")
        return
    if user["is_blocked"]:
        await message.answer(f"⚠️ Пользователь {user_id} уже заблокирован.")
        return
    await update_user(user_id, is_blocked=True)
    site_user_id = await get_site_id_by_telegram_id(user_id)
    if site_user_id:
        await block_sessions_by_site_user_id(site_user_id)
    await message.answer(f"✅ Заблокирован TG {user_id}" + (f" и SITE {site_user_id}" if site_user_id else ""))


@router.message(F.text.regexp(r"^/unban_tg_(\d+)$"))
async def cmd_unban_tg(message: Message):
    if not _is_admin(message):
        return
    user_id = int(message.text.split("_", 2)[2])
    user = await get_user(user_id)
    if not user:
        await message.answer("❌ Пользователь не найден.")
        return
    if not user["is_blocked"]:
        await message.answer(f"⚠️ Пользователь {user_id} не заблокирован.")
        return
    await update_user(user_id, is_blocked=False)
    site_user_id = await get_site_id_by_telegram_id(user_id)
    if site_user_id:
        await unblock_sessions_by_site_user_id(site_user_id)
    await message.answer(f"✅ Разблокирован TG {user_id}" + (f" и SITE {site_user_id}" if site_user_id else ""))


@router.message(F.text.regexp(r"^/ban_site_(.+)$"))
async def cmd_ban_site(message: Message):
    if not _is_admin(message):
        return
    site_user_id = message.text.split("_", 2)[2]
    blocked = await block_sessions_by_site_user_id(site_user_id)
    tg_id = await get_telegram_id_by_site_id(site_user_id)
    if tg_id:
        await update_user(tg_id, is_blocked=True)
    if blocked == 0 and not tg_id:
        await message.answer("❌ Пользователь не найден.")
        return
    await message.answer(
        f"✅ Заблокирован SITE {site_user_id} (сессии: {blocked})" +
        (f" и TG {tg_id}" if tg_id else "")
    )


@router.message(F.text.regexp(r"^/unban_site_(.+)$"))
async def cmd_unban_site(message: Message):
    if not _is_admin(message):
        return
    site_user_id = message.text.split("_", 2)[2]
    unblocked = await unblock_sessions_by_site_user_id(site_user_id)
    tg_id = await get_telegram_id_by_site_id(site_user_id)
    if tg_id:
        await update_user(tg_id, is_blocked=False)
    if unblocked == 0 and not tg_id:
        await message.answer("❌ Пользователь не найден.")
        return
    await message.answer(
        f"✅ Разблокирован SITE {site_user_id} (сессии: {unblocked})" +
        (f" и TG {tg_id}" if tg_id else "")
    )
