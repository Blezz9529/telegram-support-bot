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

    # Сбрасываем историю при новом старте
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

    # Сбрасываем флаг новой заявки и историю
    await update_user(message.from_user.id, first_message_in_ticket=1, theme=theme_key)
    await state.set_data({
        "theme": theme_key,
        "theme_name": theme_text,
        "conversation_history": []
    })
    
    await state.set_state(SupportStates.in_conversation)
    await message.answer(await load_text("ask_details"))


@router.message(SupportStates.in_conversation, F.text | F.photo | F.document | F.video)
async def handle_message_in_conversation(message: Message, state: FSMContext, bot):
    user = await get_user(message.from_user.id)
    if not user or user["is_blocked"]:
        await message.answer(await load_text("blocked"))
        return

    # Загружаем данные из FSM
    data = await state.get_data()
    current_theme = data.get("theme")
    theme_name = data.get("theme_name", "Неизвестно")
    history = data.get("conversation_history", [])

    # 📝 Логируем входящее сообщение
    media_type = "медиа" if (message.photo or message.document or message.video) else "текст"
    logger.info(f"📥 Получено {media_type} от {message.from_user.id}: {message.text or message.caption or '[Медиа]'}")

    # Добавляем новое сообщение в историю
    new_msg = {
        "from_user": True,
        "text": message.text or message.caption or "[Медиа]",
        "has_media": bool(message.photo or message.document or message.video),
        "timestamp": message.date.isoformat()
    }
    history.append(new_msg)

    # Ограничиваем историю 10 сообщениями (экономим токены)
    if len(history) > 10:
        history = history[-10:]

    # Сохраняем обновлённую историю в FSM
    await state.update_data(conversation_history=history)

    # 🔑 ВЫЗОВ ИИ-АГЕНТА С ПОЛНОЙ ИСТОРИЕЙ
    ai_result = await process_ticket(
        user_message=new_msg["text"],
        history=history,
        current_theme=current_theme,
        user_id=message.from_user.id
    )

    # 1️⃣ Обновляем тему, если ИИ её определил
    detected_theme = ai_result.get("detected_theme")
    if detected_theme and detected_theme != current_theme:
        theme_name = next((k for k, v in THEME_MAP.items() if v == detected_theme), "Другой вопрос")
        await state.update_data(theme=detected_theme, theme_name=theme_name)
        await update_user(message.from_user.id, theme=detected_theme)
        current_theme = detected_theme

    # 2️⃣ Получаем/создаём топик
    topic_id = await get_or_create_topic(
        bot, user["user_id"], user["username"], user["full_name"], theme_name
    )

    # 3️⃣ Пересылаем сообщение пользователя в топик
    await send_to_topic(bot, user, message, theme_name)

    # 4️⃣ Отправляем анализ ИИ в топик (для операторов)
    analysis_text = (
        f"🧠 <b>Анализ ИИ (gemini-2.0-flash):</b>\n"
        f"• Действие: <code>{ai_result['action']}</code>\n"
        f"• Тема: <code>{current_theme}</code>\n"
        f"• Прогресс: {len([k for k, v in ai_result['data_collected'].items() if v])}/{len(ai_result['data_collected'])} данных\n"
        f"• Не хватает: {', '.join(ai_result['missing_data']) if ai_result['missing_data'] else '—'}"
    )
    if ai_result["escalation_reason"]:
        analysis_text += f"\n• 🔴 Причина эскалации: {ai_result['escalation_reason']}"
    
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

    # 7️⃣ Отправляем пользователю
    final_response = "\n\n".join(filter(None, response_parts))
    await message.answer(final_response)
    
    # 📝 Логируем отправленный ответ
    logger.info(f"📤 Отправлен ответ пользователю {message.from_user.id}: {final_response[:100]}...")

    # 8️⃣ Сохраняем ответ бота в историю
    bot_msg = {
        "from_user": False,
        "text": final_response,
        "has_media": False,
        "timestamp": message.date.isoformat()
    }
    history.append(bot_msg)
    if len(history) > 10:
        history = history[-10:]
    await state.update_data(conversation_history=history)

    # 9️⃣ Отправляем ответ ИИ в топик (для лога)
    await bot.send_message(
        chat_id=SUPPORT_GROUP_ID,
        message_thread_id=topic_id,
        text=f"💬 <b>Ответ пользователю:</b>\n{final_response}",
        parse_mode="HTML"
    )

    # 🔟 Сбрасываем флаг первого сообщения
    if user.get("first_message_in_ticket"):
        await update_user(message.from_user.id, first_message_in_ticket=0)