# handlers/user.py
from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import get_user, create_user, update_user
from services.ai_pipeline import handle_incoming_telegram_message
from services.conversation_store import clear_conversation_events
from keyboards.reply import get_main_menu, get_feedback_keyboard, get_active_dialog_keyboard
from services.theme_map import THEME_MAP
from services.localization import load_text, load_button
import logging
from datetime import datetime, timedelta, timezone

# ✅ Объявление router
router = Router()

logger = logging.getLogger(__name__)


class SupportStates(StatesGroup):
    choosing_theme = State()
    choosing_feedback_type = State()  # 🔑 Новое состояние для выбора типа отзыва
    in_conversation = State()
    confirm_new_dialog = State()



@router.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    user = await get_user(message.from_user.id)
    if user and user["is_blocked"]:
        await message.answer(await load_text("blocked_user_response"))
        return

    # 🔑 ПРОВЕРКА: есть ли активный диалог
    current_state = await state.get_state()
    if current_state == SupportStates.in_conversation:
        # Пользователь уже в диалоге — спрашиваем подтверждение
        await state.set_state(SupportStates.confirm_new_dialog)
        await message.answer(
            await load_text("active_dialog_warning"),
            reply_markup=await get_active_dialog_keyboard()
        )
        return

    # 🔑 ПРОВЕРКА: был ли недавний диалог (менее 24 часов)
    if user and user.get("topic_id") and user.get("theme"):
        last_message_id = user.get("last_message_id", 0)
        # Если последнее сообщение было недавно — не сбрасываем
        # (проверка по времени последнего сообщения в топике)
        logger.info(f"📍 У пользователя {message.from_user.id} есть топик {user['topic_id']}")

    # Сбрасываем состояние только для нового диалога
    await state.set_data({"conversation_history": []})
    await state.set_state(SupportStates.choosing_theme)
    await message.answer(await load_text("select_theme"), reply_markup=await get_main_menu())


@router.message(SupportStates.confirm_new_dialog, F.text)
async def confirm_new_dialog(message: Message, state: FSMContext):
    new_dialog = await load_button("menu", "new_dialog")
    continue_dialog = await load_button("menu", "continue_dialog")

    if message.text == new_dialog:
        await clear_conversation_events(f"tg:{message.from_user.id}")
        await update_user(message.from_user.id, theme=None, feedback_type=None, first_message_in_ticket=1)
        await state.set_data({"conversation_history": []})
        await state.set_state(SupportStates.choosing_theme)
        await message.answer(await load_text("select_theme"), reply_markup=await get_main_menu())
        return

    if message.text == continue_dialog:
        await state.set_state(SupportStates.in_conversation)
        await message.answer("Продолжаем диалог.", reply_markup=ReplyKeyboardRemove())
        return

    await message.answer(
        await load_text("active_dialog_warning"),
        reply_markup=await get_active_dialog_keyboard()
    )


@router.message(SupportStates.choosing_theme, F.text)
async def theme_chosen(message: Message, state: FSMContext):
    theme_key = THEME_MAP.get(message.text)
    if not theme_key:
        await message.answer(await load_text("invalid_theme"))
        return

    await update_user(message.from_user.id, first_message_in_ticket=1, theme=theme_key)
    await state.update_data({"theme": theme_key, "conversation_history": []})

    # 🔑 Для отзывов — показываем кнопки выбора типа
    if theme_key == "feedback":
        await state.set_state(SupportStates.choosing_feedback_type)
        await message.answer(await load_text("feedback_type_question"), reply_markup=await get_feedback_keyboard())
    else:
        await state.set_state(SupportStates.in_conversation)
        await message.answer(await load_text("describe_problem"))


@router.message(SupportStates.choosing_feedback_type, F.text)
async def feedback_type_chosen(message: Message, state: FSMContext):
    """Обработка выбора типа отзыва"""
    feedback_type = None
    if message.text == await load_text("feedback_positive"):
        feedback_type = "positive"
    elif message.text == await load_text("feedback_negative"):
        feedback_type = "negative"
    else:
        await message.answer(await load_text("feedback_type_invalid"))
        return

    # Сохраняем тип отзыва в БД и state
    await update_user(message.from_user.id, feedback_type=feedback_type)
    await state.update_data({"feedback_type": feedback_type})

    # Переключаемся в режим диалога
    await state.set_state(SupportStates.in_conversation)
    await message.answer(await load_text("feedback_details_request"))


@router.message(SupportStates.in_conversation)
async def handle_message_in_conversation(message: Message, state: FSMContext, bot: Bot):
    user = await get_user(message.from_user.id)
    if not user or user["is_blocked"]:
        await message.answer("❌ Вы заблокированы.")
        return

    data = await state.get_data()
    current_theme = data.get("theme")

    # 🔑 ПРОВЕРКА: если топик старый (>24 часов) — предупреждаем
    if user.get("topic_id") and user.get("last_activity"):
        try:
            last_dt = datetime.fromisoformat(str(user["last_activity"]))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(tz=last_dt.tzinfo)
            hours_passed = (now - last_dt).total_seconds() / 3600
            if hours_passed > 24:
                logger.info(f"🕒 Пользователь {message.from_user.id} пишет через {hours_passed:.1f} часов")
                await message.answer(await load_text("old_topic_warning"))
                await update_user(message.from_user.id, topic_id=None, theme=None)
                await clear_conversation_events(f"tg:{message.from_user.id}")
                await state.set_data({"conversation_history": []})
                await state.set_state(SupportStates.choosing_theme)
                await message.answer(await load_text("select_theme"), reply_markup=await get_main_menu())
                return
        except Exception as e:
            logger.warning(f"⚠️ Не удалось проверить время: {e}")

    # Навигационные кнопки не должны уходить в ИИ
    nav_texts = {
        await load_button("menu", "leave_feedback"),
        await load_button("menu", "deposit_problem"),
        await load_button("menu", "how_to_play"),
        await load_button("menu", "earn_money"),
        await load_button("menu", "partnership"),
        await load_button("menu", "other_question"),
        await load_button("menu", "new_dialog"),
        await load_button("menu", "continue_dialog"),
        await load_text("feedback_positive"),
        await load_text("feedback_negative"),
    }

    is_navigation = bool(message.text) and message.text in nav_texts

    # Скачиваем медиа только для изображений
    image_bytes = None
    filename = ""
    attachment_type = None
    attachment_kind = None
    if message.photo:
        attachment_kind = "image"
        file = await bot.get_file(message.photo[-1].file_id)
        image_bytes = await bot.download_file(file.file_path)
        if hasattr(image_bytes, 'getvalue'):
            image_bytes = image_bytes.getvalue()
        filename = f"photo_{message.photo[-1].file_unique_id}.jpg"
        attachment_type = "image/jpeg"
    elif message.sticker:
        attachment_kind = "sticker"
        filename = f"sticker_{message.sticker.file_unique_id}"
        attachment_type = message.sticker.mime_type
    elif message.animation:
        attachment_kind = "gif"
        filename = message.animation.file_name or f"animation_{message.animation.file_unique_id}"
        attachment_type = message.animation.mime_type
    elif message.document:
        filename = message.document.file_name or ""
        attachment_type = message.document.mime_type
        if attachment_type and attachment_type.startswith("image/"):
            attachment_kind = "image"
            file = await bot.get_file(message.document.file_id)
            image_bytes = await bot.download_file(file.file_path)
            if hasattr(image_bytes, 'getvalue'):
                image_bytes = image_bytes.getvalue()
        else:
            attachment_kind = "other"
    elif message.video:
        attachment_kind = "other"
        attachment_type = message.video.mime_type
        filename = f"video_{message.video.file_unique_id}"
    elif message.voice:
        attachment_kind = "other"
        attachment_type = message.voice.mime_type
        filename = f"voice_{message.voice.file_unique_id}"
    elif message.video_note:
        attachment_kind = "other"
        filename = f"video_note_{message.video_note.file_unique_id}"
    elif message.audio:
        attachment_kind = "other"
        attachment_type = message.audio.mime_type
        filename = message.audio.file_name or f"audio_{message.audio.file_unique_id}"

    user_text = message.text or message.caption or ""
    if attachment_kind and attachment_kind in {"gif", "sticker", "other"}:
        label = attachment_kind.upper()
        placeholder = f"[ВЛОЖЕНИЕ: {label}] {filename or 'file'}"
        if user_text.strip():
            user_text = f"{user_text}\n{placeholder}"
        else:
            user_text = placeholder
    elif not user_text.strip() and attachment_kind:
        label = attachment_kind.upper()
        user_text = f"[ВЛОЖЕНИЕ: {label}] {filename or 'file'}"

    feedback_type = data.get("feedback_type")
    await handle_incoming_telegram_message(
        bot=bot,
        message=message,
        user=user,
        theme=current_theme or "Другой вопрос",
        feedback_type=feedback_type,
        user_text=user_text,
        image_bytes=image_bytes,
        filename=filename,
        attachment_type=attachment_type,
        attachment_kind=attachment_kind,
        visible_to_ai=not is_navigation,
    )
