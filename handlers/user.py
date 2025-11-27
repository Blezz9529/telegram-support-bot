# handlers/user.py (только изменённый блок)
@router.message(SupportStates.in_conversation, F.text | F.photo | F.document)
async def handle_message_in_conversation(message: Message, state: FSMContext, bot: Bot):
    # ... (всё до вызова process_ticket — без изменений)

    # Вызов ИИ
    ai_result = await process_ticket(
        user_message=new_msg["text"],
        history=history_for_ai,  # ← история без текущего сообщения
        current_theme=current_theme,
        user_id=message.from_user.id,
        image_bytes=image_bytes,
        filename=filename
    )

    # Обновляем тему при необходимости
    if ai_result.get("detected_theme"):
        await state.update_data(theme=ai_result["detected_theme"])
        await update_user(message.from_user.id, theme=ai_result["detected_theme"])

    # Получаем/создаём топик
    topic_id = await get_or_create_topic(
        bot, user["user_id"], user["username"], user["full_name"], current_theme or "Другой вопрос"
    )

    # Пересылаем сообщение
    await send_to_topic(bot, user, message, current_theme or "Другой вопрос")

    # === ФОРМИРУЕМ ОТВЕТ В ТОПИК ===
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

    # === УВЕДОМЛЕНИЕ ОПЕРАТОРОВ — ТОЛЬКО ПРИ ЭСКАЛАЦИИ ===
    action = ai_result.get("action", "").lower()
    if action == "escalate":
        admin_tags = " ".join([f"<a href='tg://user?id={a}'>❗</a>" for a in ADMINS])
        reason = ai_result.get("escalation_reason") or "автоматическая эскалация"
        await bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            message_thread_id=topic_id,
            text=f"{admin_tags} <b>❗ УВЕДОМЛЕНИЕ ОПЕРАТОРА</b>\n{reason}",
            parse_mode="HTML"
        )

    # Ответ пользователю
    response_parts = []
    if user.get("first_message_in_ticket") and ai_result.get("estimated_time"):
        response_parts.append(f"ℹ️ Время обработки — до {ai_result['estimated_time']}.")
    response_parts.append(ai_result["response_to_user"])
    final_response = "\n\n".join(filter(None, response_parts))

    if not final_response.strip():
        final_response = "Спасибо за информацию. Оператор скоро свяжется с вами."

    await message.answer(final_response)

    # Обновляем историю и флаг
    history.append({"from_user": False, "text": final_response, "has_media": False})
    await state.update_data(conversation_history=history[-10:])
    if user.get("first_message_in_ticket"):
        await update_user(message.from_user.id, first_message_in_ticket=0)