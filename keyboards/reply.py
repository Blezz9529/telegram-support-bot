# keyboards/reply.py
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from services.localization import load_button
import asyncio

async def get_main_menu() -> ReplyKeyboardMarkup:
    """Основное меню — 2 колонки, адаптивно"""
    btns = [
        await load_button("menu", "leave_feedback"),
        await load_button("menu", "deposit_problem"),
        await load_button("menu", "how_to_play"),
        await load_button("menu", "earn_money"),
        await load_button("menu", "partnership"),
        await load_button("menu", "other_question"),
    ]
    # Разбиваем на 2 колонки: [ [btn1, btn2, btn3], [btn4, btn5, btn6] ]
    keyboard = [
        [KeyboardButton(text=btns[i]), KeyboardButton(text=btns[i+3])]
        for i in range(3)
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=False  # остаётся, пока не скроем явно
    )


async def get_feedback_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для отзывов: Положительный / Отрицательный"""
    keyboard = [
        [KeyboardButton(text="😊 Положительный"), KeyboardButton(text="😞 Отрицательный")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=True  # исчезает после нажатия
    )


async def get_new_ticket_button() -> ReplyKeyboardMarkup:
    """Клавиатура после выбора темы: только «Новая заявка»"""
    btn = await load_button("menu", "new_ticket")
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=btn)]],
        resize_keyboard=True,
        one_time_keyboard=True  # исчезает после нажатия
    )


async def get_active_dialog_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура подтверждения: продолжить диалог или начать новый"""
    new_dialog = await load_button("menu", "new_dialog")
    continue_dialog = await load_button("menu", "continue_dialog")
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=new_dialog), KeyboardButton(text=continue_dialog)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
