# handlers/user.py
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import get_user, create_user, update_user
from services.localization import load_text
from services.ai_agent import process_ticket
from services.forum import send_to_topic, get_or_create_topic
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
    await message.answer("Выберите тему:", reply_markup=[
        [F.text for F in [F.text for F in []]]  # Упрощённо — замените на вашу клавиатуру
    ])

@router.message(SupportStates.choosing_theme, F.text)
async def theme_chosen(message: Message, state: FSMContext):
    theme_key = THEME_MAP.get(message.text)
    if not theme_key:
        await message.answer("Выберите тему из меню.")
        return
    await update_user(message.from_user.id, first_message_in_ticket=1, theme=theme_key)
    await state.set_data({
        "theme": theme_key,
        "theme_name": message.text,
        "conversation_history": []
    })
    await state.set_state(SupportStates.in_conversation)
    await message.answer("Опишите проблему.")

@router.message(SupportStates.in_conversation)
async def handle_message_in_conversation(message: Message, state: FSMContext, bot):
    user = await get_user(message.from_user.id)
    if not user or user["is_blocked"]:
        await message.answer("Вы заблокированы.")
        return

    data = await state.get_data()
    current_theme = data.get("theme")
    history = data.get("conversation_history", [])

    new_msg = {
        "from_user": True,
        "text": message.text or message.caption or "[Медиа]",
        "has_media": bool(message.photo or message.document),
        "timestamp": str(message.date)
    }
    history.append(new_msg)
    if len(history) > 10:
        history = history[-10:]
    await state.update_data(conversation_history=history)

    ai_result = await process_ticket(
        user_message=new_msg["text"],
        history=history,
        current_theme=current_theme,
        user_id=message.from_user.id
    )

    # Обновляем тему
    if ai_result.get("detected_theme") and ai_result["detected_theme"] != current_theme:
        theme_name = next((k for k,v in THEME_MAP.items() if v == ai_result["detected_theme"]), "Другой вопрос")
        await state.update_data(theme=ai_result["detected_theme"], theme_name=theme_name)
        await update_user(message.from_user.id, theme=ai_result["detected_theme"])
        current_theme = ai_result["detected_theme"]

    # Отправляем в топик (оптимизировано)
    topic_id = await get_or_create_topic(bot, user["user_id"], user["username"], user["full_name"], data.get("theme_name", "—"))
    await send_to_topic(bot, user, message, data.get("theme_name", "—"))

    # Анализ ИИ в топик
    analysis = [
        f"🧠 <b>ИИ (gemini-2.0-flash):</b>",
        f"• Действие: <code>{ai_result['action']}</code>",
        f"• Тема: <code>{current_theme}</code>"
    ]
    if ai_result["missing_data"]:
        analysis.append(f"• Не хватает: {', '.join(ai_result['missing_data'])}")
    if ai_result["escalation_reason"]:
        analysis.append(f"• 🔴 Причина: {ai_result['escalation_reason']}")

    await bot.send_message(
        chat_id=SUPPORT_GROUP_ID,
        text="\n".join(analysis),
        message_thread_id=topic_id,
        parse_mode="HTML"
    )

    # Эскалация
    if ai_result["action"] == "escalate" and ai_result["escalation_reason"]:
        admin_tags = " ".join([f"<a href='tg://user?id={a}'>❗</a>" for a in ADMINS])
        await bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            text=f"{admin_tags} <b>❗ ДЕЙСТВИЕ:</b>\n{ai_result['escalation_reason']}",
            message_thread_id=topic_id,
            parse_mode="HTML"
        )

    # Ответ пользователю
    response_parts = []
    if user.get("first_message_in_ticket") and ai_result["estimated_time"]:
        response_parts.append(f"ℹ️ Время обработки — до {ai_result['estimated_time']}.")
    response_parts.append(ai_result["response_to_user"])
    final_response = "\n\n".join(response_parts)
    await message.answer(final_response)

    # Сохраняем ответ бота в историю
    history.append({
        "from_user": False,
        "text": final_response,
        "has_media": False,
        "timestamp": str(message.date)
    })
    await state.update_data(conversation_history=history[-10:])

    if user.get("first_message_in_ticket"):
        await update_user(message.from_user.id, first_message_in_ticket=0)