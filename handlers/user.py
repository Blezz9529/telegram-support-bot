# handlers/user.py
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import get_user, create_user, update_user
from services.localization import load_text, load_button
from services.ai_agent import ask_ai
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

    data = await state.get_data()
    theme_name = data.get("theme_name", "Неизвестно")

    # 1️⃣ Пересылаем сообщение пользователя в топик
    await send_to_topic(bot, user, message, theme_name)

    # 2️⃣ Получаем ответ от ИИ
    history = [{"role": "user", "content": message.text or message.caption or ""}]
    ai_response = await ask_ai(
        user_message=message.text or message.caption or "",
        history=history,
        theme=theme_name,
        user_id=message.from_user.id
    )

    # 3️⃣ Обработка кодового слова [[PUSH_OPERATOR]]
    need_push_operator = "[[PUSH_OPERATOR]]" in ai_response["response_to_user"]
    if need_push_operator:
        # Удаляем кодовое слово из ответа пользователю
        ai_response["response_to_user"] = (
            ai_response["response_to_user"]
            .replace("[[PUSH_OPERATOR]]", "")
            .strip()
        )
        # Отправляем уведомление операторам в топик
        topic_id = await get_or_create_topic(
            bot, user["user_id"], user["username"], user["full_name"], theme_name
        )
        admin_tags = " ".join([f"<a href='tg://user?id={a}'>🔔</a>" for a in ADMINS])
        await bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            message_thread_id=topic_id,
            text=f"{admin_tags} <b>❗ Требуется внимание оператора!</b>",
            parse_mode="HTML"
        )

    # 4️⃣ Отправляем ответ ИИ в ТОТ ЖЕ ТОПИК (для лога)
    topic_id = await get_or_create_topic(
        bot, user["user_id"], user["username"], user["full_name"], theme_name
    )
    await bot.send_message(
        chat_id=SUPPORT_GROUP_ID,
        message_thread_id=topic_id,
        text=f"🤖 <b>ИИ:</b>\n{ai_response['response_to_user']}",
        parse_mode="HTML"
    )
    if ai_response.get("need_more_info") and ai_response.get("additional_questions"):
        await bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            message_thread_id=topic_id,
            text=f"❓ <b>ИИ (уточнение):</b>\n{ai_response['additional_questions']}",
            parse_mode="HTML"
        )

    # 5️⃣ Формируем ответ ПОЛЬЗОВАТЕЛЮ (без дублирования!)
    response_parts = []

    # Уведомление о времени — ТОЛЬКО при первом сообщении в заявке
    if user.get("first_message_in_ticket") and ai_response.get("estimated_time"):
        notice = await load_text("ticket_notice", time=ai_response["estimated_time"])
        response_parts.append(notice)

    # Основной текст: при need_more_info — используем ТОЛЬКО additional_questions
    if ai_response.get("need_more_info") and ai_response.get("additional_questions"):
        response_parts.append(ai_response["additional_questions"])
    else:
        response_parts.append(ai_response["response_to_user"])

    await message.answer("\n".join(filter(None, response_parts)))

    # 6️⃣ Сбрасываем флаг первого сообщения
    if user.get("first_message_in_ticket"):
        await update_user(message.from_user.id, first_message_in_ticket=0)