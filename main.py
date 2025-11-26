# main.py
import asyncio
import logging
import sys
import os
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from config import BOT_TOKEN, SUPPORT_GROUP_ID

# Настройка логгера
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ✅ Добавляем google в sys.path, если используется локальный __init__.py
if os.path.exists("google/__init__.py"):
    sys.path.insert(0, ".")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)
dp = Dispatcher()


# Импорт хэндлеров
try:
    from handlers import user, admin
    dp.include_router(user.router)
    dp.include_router(admin.router)
except Exception as e:
    logger.critical(f"❌ Ошибка импорта handlers: {e}")
    sys.exit(1)


async def on_startup():
    logger.info("🚀 Инициализация бота...")
    try:
        from storages.db import init_db
        await init_db()
        logger.info("✅ База данных готова")

        chat = await bot.get_chat(SUPPORT_GROUP_ID)
        logger.info(f"✅ Форум: {chat.title}")
    except Exception as e:
        logger.critical(f"❌ Ошибка инициализации: {e}")
        sys.exit(1)


async def main():
    await on_startup()
    logger.info("📡 Бот запущен и ожидает сообщений...")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query"],
            close_bot_session=True
        )
    except KeyboardInterrupt:
        logger.info("🛑 Получен сигнал завершения")
    except Exception as e:
        logger.exception(f"💥 Критическая ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "event loop is closed" in str(e):
            pass
        else:
            raise