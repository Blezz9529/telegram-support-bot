# handlers/user.py
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import SUPPORT_GROUP_ID
from storages.db import get_user, create_user, update_user
from services.localization import load_text, load_button
from services.ai_agent import ask_ai
from services.forum import send_to_topic
from keyboards.reply import get_main_menu

router = Router()

class SupportStates(StatesGroup):
    choosing_theme = State()
    in_conversation = State()  # ← ОДНО состояние на весь диалог

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

    # Сбрасываем флаг — новая заявка
    await update_user(message.from_user.id, first_message_in_ticket=1)

    await state.update_data(theme=theme_key, theme_name=theme_text)
    await state.set_state(SupportStates.in_conversation)
    await message.answer(await load_text("ask_details"))

# ✅ ОСНОВНОЙ ХЭНДЛЕР: все сообщения в диалоге
@router.message(SupportStates.in_conversation, F.text | F.photo | F.document | F.video)
async def handle_message_in_conversation(message: Message, state: FSMContext, bot):
    user = await get_user(message.from_user.id)
    if not user or user["is_blocked"]:
        await message.answer(await load_text("blocked"))
        return

    data = await state.get_data()
    theme_name = data.get("theme_name", "Неизвестно")

    # 1️⃣ Пересылаем ВСЕ сообщения в топик (диалог)
    await send_to_topic(bot, user, message, theme_name)

    # 2️⃣ Получаем ответ от ИИ на КАЖДОЕ сообщение
    history = [{"role": "user", "content": message.text or message.caption or ""}]
    ai_response = await ask_ai(
        user_message=message.text or message.caption or "",
        history=history,
        theme=theme_name,
        user_id=message.from_user.id
    )

    # 3️⃣ Отправляем ответ ИИ пользователю
    #    + уведомление о времени — ТОЛЬКО при first_message_in_ticket
    response_texts = [ai_response["response_to_user"]]

    if user.get("first_message_in_ticket") and ai_response.get("estimated_time"):
        # Формируем уведомление один раз
        est_time = ai_response["estimated_time"]
        notice = await load_text("ticket_notice", time=est_time)
        response_texts.insert(0, notice)

    # Отправляем всё как единое сообщение
    full_response = "\n\n".join(filter(None, response_texts))
    await message.answer(full_response)

    # Сбрасываем флаг после первого сообщения
    if user.get("first_message_in_ticket"):
        await update_user(message.from_user.id, first_message_in_ticket=0)

    # Продолжаем в том же состоянии — никакого clear()