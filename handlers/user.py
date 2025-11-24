# handlers/user.py
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import get_user, create_user, update_user
from services.localization import load_text, load_button
from services.ai_agent import process_ticket
from services.forum import send_to_topic, get_or_create_topic
from keyboards.reply import get_main_menu

router = Router()

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

    # Сбрасываем флаг новой заявки
    await update_user(message.from_user.id, first_message_in_ticket=1, theme=theme_key)

    await state.update_data(theme=theme_key, theme_name=theme_text)
    await state.set_state(SupportStates.in_conversation)
    await message.answer(await load_text("ask_details"))


@router.message(SupportStates.in_conversation, F.text | F.photo | F.document | F.video)
async def handle_message_in_conversation(message: Message, state: FSMContext, bot):
    user = await get_user(message.from_user.id)
    if not user or user["is_blocked"]:
        await message.answer(await load_text("blocked"))
        return

    # Получаем текущую тему
    data = await state.get_data()
    current_theme = data.get("theme")
    theme_name = data.get("theme_name", "Неизвестно")

    # Формируем историю (упрощённо: только текущее сообщение)
    # В продакшене: загружать из БД последние 5 сообщений
    history = [{
        "from_user": True,
        "text": message.text or message.caption or "[Медиа]",
        "has_media": message.photo or message.document or message.video
    }]

    # 🔑 ЕДИНЫЙ ВЫЗОВ ОБРАБОТКИ ЗАЯВКИ
    ai_result = await process_ticket(
        user_message=message.text or message.caption or "[Пользователь отправил медиа]",
        history=history,
        current_theme=current_theme,
        user_id=message.from_user.id
    )

    # 1️⃣ Обновляем тему, если ИИ её определил
    detected_theme = ai_result.get("detected_theme")
    if detected_theme and detected_theme != current_theme:
        # Обновляем в FSM и БД
        theme_name = next((k for k, v in THEME_MAP.items() if v == detected_theme), "Другой вопрос")
        await state.update_data(theme=detected_theme, theme_name=theme_name)
        await update_user(message.from_user.id, theme=detected_theme)
        current_theme = detected_theme
        theme_name = theme_name  # обновляем для отправки в топик

    # 2️⃣ Получаем/создаём топик
    topic_id = await get_or_create_topic(
        bot, user["user_id"], user["username"], user["full_name"], theme_name
    )

    # 3️⃣ Пересылаем сообщение пользователя в топик
    await send_to_topic(bot, user, message, theme_name)

    # 4️⃣ Отправляем анализ ИИ в топик (для операторов)
    analysis_text = (
        f"🧠 <b>Анализ ИИ:</b>\n"
        f"• Действие: <code>{ai_result['action']}</code>\n"
        f"• Тема: <code>{current_theme}</code>\n"
        f"• Собранные данные: {len([k for k, v in ai_result['data_collected'].items() if v])}/{len(ai_result['data_collected'])}\n"
        f"• Не хватает: {', '.join(ai_result['missing_data']) if ai_result['missing_data'] else '—'}"
    )
    if ai_result["escalation_reason"]:
        analysis_text += f"\n• 🔴 Эскалация: {ai_result['escalation_reason']}"
    
    await bot.send_message(
        chat_id=SUPPORT_GROUP_ID,
        message_thread_id=topic_id,
        text=analysis_text,
        parse_mode="HTML"
    )

    # 5️⃣ Эскалация → уведомление операторов
    if ai_result["action"] == "escalate" and ai_result["escalation_reason"]:
        admin_tags = " ".join([f"<a href='tg://user?id={a}'>❗</a>" for a in ADMINS])
        await bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            message_thread_id=topic_id,
            text=f"{admin_tags} <b>❗ ТРЕБУЕТСЯ ДЕЙСТВИЕ:</b>\n{ai_result['escalation_reason']}",
            parse_mode="HTML"
        )

    # 6️⃣ Формируем ответ пользователю
    response_parts = []

    # Уведомление о времени — ТОЛЬКО при первом сообщении в заявке
    if user.get("first_message_in_ticket") and ai_result["estimated_time"]:
        notice = await load_text("ticket_notice", time=ai_result["estimated_time"])
        response_parts.append(notice)

    # Основной ответ
    response_parts.append(ai_result["response_to_user"])

    # Отправляем пользователю
    await message.answer("\n\n".join(filter(None, response_parts)))

    # 7️⃣ Отправляем ответ ИИ в топик (для лога)
    await bot.send_message(
        chat_id=SUPPORT_GROUP_ID,
        message_thread_id=topic_id,
        text=f"💬 <b>Ответ ИИ пользователю:</b>\n{ai_result['response_to_user']}",
        parse_mode="HTML"
    )

    # 8️⃣ Сбрасываем флаг первого сообщения
    if user.get("first_message_in_ticket"):
        await update_user(message.from_user.id, first_message_in_ticket=0)