from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from services.localization import load_button
import asyncio

async def get_main_menu() -> ReplyKeyboardMarkup:
    btns = [
        await load_button("menu", "leave_feedback"),
        await load_button("menu", "deposit_problem"),
        await load_button("menu", "how_to_play"),
        await load_button("menu", "earn_money"),
        await load_button("menu", "partnership"),
        await load_button("menu", "other_question"),
    ]
    keyboard = [[KeyboardButton(text=btn)] for btn in btns]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)