# handlers/admin.py
from aiogram import Router, Bot, F  # ← ДОБАВЛЕН импорт F
from aiogram.types import Message, CallbackQuery
from config import SUPPORT_GROUP_ID, ADMINS
from storages.db import get_user, update_user
from services.localization import load_text

router = Router()

# Фильтр: только сообщения из SUPPORT_GROUP_ID и в топиках
@router.message(
    F.chat.id == SUPPORT_GROUP_ID,        # ← Теперь F определён
    F.message_thread_id,
    F.reply_to_message
)
async def handle_admin_reply(message: Message, bot: Bot):
    # ... (остальное без изменений)