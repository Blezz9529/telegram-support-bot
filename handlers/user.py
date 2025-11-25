# handlers/user.py
from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import get_user, create_user, update_user
from services.localization import load_text
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

    # Сбрасываем FSM
    await state.set_data({"conversation_history": []})
    await state.set_state(SupportStates.choosing_theme)

    # Отправляем меню
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

    # Сбрасываем флаг новой заявки и сохраняем тему
    await update_user(message.from_user.id, first_message_in_ticket=1, theme=theme_key)
    await state.update_data({
        "theme": theme_key,
        "theme_name": theme_text,
        "conversation_history": []
    })
    await state.set_state(SupportStates.in_conversation)
    await message.answer(await load_text("ask_details"))


@router.message(SupportStates.in_conversation, F.text | F.photo | F.document | F.video)
async def handle_message_in_conversation(message: Message, state: FSMContext, bot: Bot):
    user = await get_user(message.from_user.id)
    if not user or user["is_blocked"]:
        await message.answer(await load_text("blocked"))
        return

    # Получаем данные из FSM
    data = await state.get_data()
    current_theme = data.get("theme")
    theme_name = data.get("theme_name", "Неизвестно")
    history = data.get("conversation_history", [])

    # 🔑 Формируем запись сообщения (важно: has_media=True для медиа)
    new_msg = {
        "from_user": True,
        "text": message.text or message.caption or "[Медиа]",
        "has_media": bool(message.photo or message.document or message.video),
        "timestamp": message.date.isoformat()
    }
    history.append(new_msg)
    if len(history) > 10:
        history = history[-10:]
    await state.update_data(conversation_history=history)

    # 🔑 ВЫЗОВ ИИ (асинхронный!)
    try:
        ai_result = await process_ticket(
            user_message=new_msg["text"],
            history=history,
            current_theme=current_theme,
            user_id=message.from_user.id
        )
    except Exception as e:
        logger.exception("❌ Ошибка в process_ticket — используем fallback")
        ai_result = {
            "action": "escalate",
            "response_to_user": "Извините, произошла ошибка. Запрос передан оператору.",
            "detected_theme": current_theme,
            "data_collected": {},
            "missing_data": [],
            "escalation_reason": "internal_error",
            "estimated_time": "12 часов"
        }

    # Обновление темы, если определена ИИ
    detected_theme = ai_result.get("detected_theme")
    if detected_theme and detected_theme != current_theme:
        theme_name = next((k for k, v in THEME_MAP.items() if v == detected_theme), "Другой вопрос")
        await state.update_data(theme=detected_theme, theme_name=theme_name)
        await update_user(message.from_user.id, theme=detected_theme)
        current_theme = detected_theme

    # Получаем/создаём топик
    topic_id = await get_or_create_topic(
        bot, user["user_id"], user["username"], user["full_name"], theme_name
    )

    # Пересылаем сообщение пользователя в топик
    await send_to_topic(bot, user, message, theme_name)

    # ✅ ФОРМИРУЕМ ПОЛНЫЙ ОТВЕТ ИИ ДЛЯ ТОПИКА
    ai_response_text = ai_result["response_to_user"].strip()
    
    # Добавляем уточнения, если есть
    if ai_result.get("missing_data"):
        ai_response_text += "\n\n❓ Запрошены: " + ", ".join(ai_result["missing_data"])
    
    if ai_result.get("escalation_reason"):
        ai_response_text += "\n\n🔴 Причина эскалации: " + ai_result["escalation_reason"]
    
    # Добавляем estimated_time, если есть
    time_info = ai_result.get("estimated_time")
    if time_info:
        ai_response_text += f"\n\n⏱ Время обработки: {time_info}"

    # Отправляем единое сообщение в топик
    ai_log = (
        f"🧠 <b>ИИ (gemini-2.0-flash)</b>\n"
        f"• Действие: <code>{ai_result['action']}</code>\n"
        f"• Тема: <code>{current_theme}</code>\n"
        f"──────────────────\n"
        f"{ai_response_text}"
    )

    await bot.send_message(
        chat_id=SUPPORT_GROUP_ID,
        message_thread_id=topic_id,
        text=ai_log,
        parse_mode="HTML"
    )

    # ✅ УВЕДОМЛЕНИЕ АДМИНОВ — ТОЛЬКО ПРИ ЭСКАЛАЦИИ
    if ai_result.get("action") == "escalate" and ai_result.get("escalation_reason"):
        admin_tags = " ".join([f"<a href='tg://user?id={a}'>❗</a>" for a in ADMINS])
        await bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            message_thread_id=topic_id,
            text=f"{admin_tags} <b>❗ ТРЕБУЕТСЯ ДЕЙСТВИЕ:</b>\n<i>{ai_result['escalation_reason']}</i>",
            parse_mode="HTML"
        )

    # ✅ Формируем ответ пользователю
    response_parts = []
    # Уведомление о времени — ТОЛЬКО при первом сообщении в заявке
    if user.get("first_message_in_ticket") and ai_result.get("estimated_time"):
        notice = await load_text("ticket_notice", time=ai_result["estimated_time"])
        response_parts.append(notice)
    
    response_parts.append(ai_result["response_to_user"])
    final_response = "\n\n".join(filter(None, response_parts))

    # Отправляем пользователю
    await message.answer(final_response)

    # Сохраняем ответ бота в историю
    history.append({
        "from_user": False,
        "text": final_response,
        "has_media": False,
        "timestamp": message.date.isoformat()
    })
    await state.update_data(conversation_history=history[-10:])

    # Сбрасываем флаг первого сообщения
    if user.get("first_message_in_ticket"):
        await update_user(message.from_user.id, first_message_in_ticket=0)