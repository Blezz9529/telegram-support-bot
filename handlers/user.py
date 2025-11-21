from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
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
    providing_details = State()
    awaiting_info = State()  # если ИИ запросил доп. инфо

# Соответствие кнопок → темам
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

    await state.update_data(theme=theme_key, theme_name=theme_text)
    await state.set_state(SupportStates.providing_details)
    await message.answer(await load_text("ask_details"))

@router.message(SupportStates.providing_details, F.text | F.photo | F.document | F.video)
async def handle_user_message(message: Message, state: FSMContext, bot):
    user = await get_user(message.from_user.id)
    if not user or user["is_blocked"]:
        await message.answer(await load_text("blocked"))
        return

    data = await state.get_data()
    theme_name = data.get("theme_name", "Неизвестно")

    # Получаем историю (здесь — только последнее сообщение; можно расширить)
    history = [{"role": "user", "content": message.text or message.caption or ""}]

    # Запрашиваем ИИ
    ai_response = await ask_ai(
        user_message=message.text or message.caption or "",
        history=history,
        theme=theme_name,
        user_id=message.from_user.id
    )

    # Отправляем ответ ИИ пользователю
    if ai_response["need_more_info"]:
        await message.answer(ai_response["response_to_user"])
        await message.answer(ai_response["additional_questions"])
        await state.set_state(SupportStates.awaiting_info)
        return

    elif not ai_response["need_human"]:
        await message.answer(await load_text("auto_answer_resolved"))
        await message.answer(ai_response["response_to_user"])
        return

    else:
        # Нужен человек — пересылаем в топик
        forwarded_msg_id = await send_to_topic(bot, user, message, theme_name)
        await message.answer(await load_text("forwarded_to_human"))
        await state.clear()  # выход из FSM
        return

@router.message(SupportStates.awaiting_info, F.text | F.photo | F.document | F.video)
async def handle_additional_info(message: Message, state: FSMContext, bot):
    # Можно отправить доп. инфо в ИИ или сразу в топик
    user = await get_user(message.from_user.id)
    if not user or user["is_blocked"]:
        await message.answer(await load_text("blocked"))
        return

    data = await state.get_data()
    theme_name = data.get("theme_name", "Неизвестно")

    # Просто пересылаем как продолжение
    await send_to_topic(bot, user, message, theme_name)
    await message.answer(await load_text("message_forwarded"))
    await state.clear()