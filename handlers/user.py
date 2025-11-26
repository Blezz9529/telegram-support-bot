# handlers/user.py
from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import get_user, create_user, update_user
from services.localization import load_text, load_button
from services.ai_agent import process_ticket
from services.forum import send_to_topic, get_or_create_topic
from keyboards.reply import get_main_menu, get_new_ticket_button
import logging

router = Router()
logger = logging.getLogger(__name__)


class SupportStates(StatesGroup):
    choosing_theme = State()
    in_conversation = State()


THEME_MAP = {
    "Оставить отзыв": "feedback",
    "Проблема с пополнением": "deposit",
    "Как играть": "how_to_play",
    "Хочу заработать": "earn",
    "Предлагаю сотрудничество": "partnership",
    "Другой вопрос": "other"
}


@router.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    user = await get_user(message.from_user.id)
    if user and user["is_blocked"]:
        await message.answer(await load_text("blocked"))
        return

    await state.set_data({"conversation_history": []})
    await state.set_state(SupportStates.choosing_theme)

    await message.answer(
        await load_text("start_message"),
        reply_markup=await get_main_menu()
    )


@router.message(SupportStates.choosing_theme, F.text)
async def theme_chosen(message: Message, state: FSMContext):
    theme_text = message.text
    theme_key = THEME_MAP.get(theme_text)
    if not theme_key:
        await message.answer("Пожалуйста, выберите тему из меню.")
        return

    await update_user(message.from_user.id, first_message_in_ticket=1, theme=theme_key)
    await state.update_data({
        "theme": theme_key,
        "theme_name": theme_text,
        "conversation_history": []
    })
    await state.set_state(SupportStates.in_conversation)

    # ✅ Отправляем сообщение + скрываем старую клавиатуру
    await message.answer(
        await load_text("ask_details"),
        reply_markup=await get_new_ticket_button()  # ← только «Новая заявка» после выбора
    )


@router.message(SupportStates.in_conversation, F.text == "Новая заявка")
async def new_ticket_request(message: Message, state: FSMContext):
    """Обработка кнопки «Новая заявка» в середине диалога"""
    # Сбрасываем состояние и возвращаем к выбору темы
    await state.set_state(SupportStates.choosing_theme)
    await message.answer(
        "Выберите новую тему:",
        reply_markup=await get_main_menu()
    )


@router.message(SupportStates.in_conversation, F.text | F.photo | F.document | F.video)
async def handle_message_in_conversation(message: Message, state: FSMContext, bot: Bot):
    user = await get_user(message.from_user.id)
    if not user or user["is_blocked"]:
        await message.answer(await load_text("blocked"))
        return

    data = await state.get_data()
    current_theme = data.get("theme")
    theme_name = data.get("theme_name", "Неизвестно")
    history = data.get("conversation_history", [])

    # Скачиваем медиа
    image_bytes = None
    filename = ""
    if message.photo:
        file = await bot.get_file(message.photo[-1].file_id)
        image_bytes = await bot.download_file(file.file_path)
        if hasattr(image_bytes, 'getvalue'):
            image_bytes = image_bytes.getvalue()
        filename = f"photo_{message.photo[-1].file_unique_id}.jpg"
    elif message.document:
        file = await bot.get_file(message.document.file_id)
        image_bytes = await bot.download_file(file.file_path)
        if hasattr(image_bytes, 'getvalue'):
            image_bytes = image_bytes.getvalue()
        filename = message.document.file_name or ""

    # Подготавливаем запись сообщения (не добавляем в историю до вызова ИИ!)
    new_msg = {
        "from_user": True,
        "text": message.text or message.caption or "",
        "has_media": bool(image_bytes),
        "timestamp": message.date.isoformat()
    }

    # Подготовка истории ДЛЯ ИИ (без current message, с заменой медиа на [ИЗОБРАЖЕНИЕ])
    history_for_ai = []
    for msg in history:  # ← только прошлые сообщения
        clean_msg = msg.copy()
        if msg.get("has_media"):
            clean_msg["text"] = "[ИЗОБРАЖЕНИЕ]"
        history_for_ai.append(clean_msg)

    # Вызов ИИ
    ai_result = await process_ticket(
        user_message=new_msg["text"],
        history=history_for_ai,
        current_theme=current_theme,
        user_id=message.from_user.id,
        image_bytes=image_bytes,
        filename=filename
    )

    # Только после вызова — обновляем историю
    history.append(new_msg)
    if len(history) > 10:
        history = history[-10:]
    await state.update_data(conversation_history=history)

    # Обновление темы при необходимости
    detected_theme = ai_result.get("detected_theme")
    if detected_theme and detected_theme != current_theme:
        theme_name = next((k for k, v in THEME_MAP.items() if v == detected_theme), "Другой вопрос")
        await state.update_data(theme=detected_theme, theme_name=theme_name)
        await update_user(message.from_user.id, theme=detected_theme)
        current_theme = detected_theme

    # Получаем / создаём топик
    topic_id = await get_or_create_topic(
        bot, user["user_id"], user["username"], user["full_name"], theme_name
    )

    # Пересылаем сообщение
    await send_to_topic(bot, user, message, theme_name)

    # === ОТВЕТ В ТОПИК ===
    ai_text = ai_result["response_to_user"].strip()
    if ai_result.get("escalation_reason"):
        ai_text += f"\n\n🔴 Причина эскалации: {ai_result['escalation_reason']}"
    if ai_result.get("estimated_time"):
        ai_text += f"\n\n⏱ Время обработки: {ai_result['estimated_time']}"

    await bot.send_message(
        chat_id=SUPPORT_GROUP_ID,
        message_thread_id=topic_id,
        text=f"🧠 <b>ИИ</b>\n{ai_text}",
        parse_mode="HTML"
    )

    # Уведомление операторов при эскалации
    action = ai_result.get("action", "").lower()
    escalation_reason = ai_result.get("escalation_reason") or ""
    if action == "escalate" or "оператор" in ai_result.get("response_to_user", "").lower():
        admin_tags = " ".join([f"<a href='tg://user?id={a}'>❗</a>" for a in ADMINS])
        reason = escalation_reason or "автоматическая эскалация"
        await bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            message_thread_id=topic_id,
            text=f"{admin_tags} <b>❗ УВЕДОМЛЕНИЕ ОПЕРАТОРА</b>\n{reason}",
            parse_mode="HTML"
        )

    # Ответ пользователю — с защитой от пустого текста
    response_parts = []
    if user.get("first_message_in_ticket") and ai_result.get("estimated_time"):
        notice = await load_text("ticket_notice", time=ai_result["estimated_time"])
        response_parts.append(notice)
    response_parts.append(ai_result["response_to_user"])
    final_response = "\n\n".join(filter(None, response_parts))

    if not final_response.strip():
        final_response = "Спасибо за информацию. Оператор скоро свяжется с вами."

    # ✅ Отправляем ответ + оставляем клавиатуру «Новая заявка»
    await message.answer(
        final_response,
        reply_markup=await get_new_ticket_button()
    )

    # Обновляем историю и флаг
    history.append({"from_user": False, "text": final_response, "has_media": False})
    await state.update_data(conversation_history=history[-10:])
    if user.get("first_message_in_ticket"):
        await update_user(message.from_user.id, first_message_in_ticket=0)