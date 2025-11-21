# handlers/admin.py
from aiogram import Router, Bot, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import get_user, update_user
from services.localization import load_text

router = Router()

# Фильтр: только сообщения из SUPPORT_GROUP_ID, в топиках, и с reply
@router.message(
    F.chat.id == SUPPORT_GROUP_ID,
    F.message_thread_id,
    F.reply_to_message
)
async def handle_admin_reply(message: Message, bot: Bot):
    """Обработка ответов админов в топиках"""
    reply = message.reply_to_message

    # Проверяем: ответ именно на пересланное сообщение от пользователя
    if not reply or not reply.forward_from:
        await message.reply(await load_text("invalid_reply"))
        return

    user_id = reply.forward_from.id
    user = await get_user(user_id)

    if not user:
        await message.reply(f"⚠️ Пользователь {user_id} не найден в БД.")
        return

    if user["is_blocked"]:
        await message.reply(f"❌ Пользователь {user_id} заблокирован — ответ не отправлен.")
        return

    # Отправляем ответ пользователю
    try:
        if message.text:
            await bot.send_message(user_id, f"💬 <b>Ответ оператора:</b>\n{message.text}", parse_mode="HTML")
        elif message.photo:
            await bot.send_photo(
                user_id,
                photo=message.photo[-1].file_id,
                caption=message.caption or "",
                parse_mode="HTML"
            )
        elif message.document:
            await bot.send_document(
                user_id,
                document=message.document.file_id,
                caption=message.caption or "",
                parse_mode="HTML"
            )
        elif message.video:
            await bot.send_video(
                user_id,
                video=message.video.file_id,
                caption=message.caption or "",
                parse_mode="HTML"
            )
        else:
            await message.reply("⚠️ Поддерживаются только текст, фото, документы и видео.")
            return

        await message.reply(await load_text("reply_sent_to_user"))
    except Exception as e:
        await message.reply(f"❌ Ошибка отправки: {e}")


@router.callback_query(F.data.startswith("block_user:"))
async def block_user(callback: CallbackQuery, bot: Bot):
    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Некорректный ID", show_alert=True)
        return

    user = await get_user(user_id)
    if not user:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    if user["is_blocked"]:
        await callback.answer(await load_text("already_blocked", user_id=user_id), show_alert=True)
        return

    await update_user(user_id, is_blocked=True)
    try:
        await bot.send_message(user_id, await load_text("blocked"))
    except Exception:
        # Пользователь мог заблокировать бота — не критично
        pass

    await callback.answer(await load_text("user_blocked_success", user_id=user_id), show_alert=True)
    # Убираем кнопку после нажатия
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)