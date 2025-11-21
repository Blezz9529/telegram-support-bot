# main.py
import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from config import BOT_TOKEN

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# Импорты после создания bot/dp (чтобы избежать циклических импортов при ошибках)
from handlers import user, admin

dp.include_router(user.router)
dp.include_router(admin.router)

async def main():
    try:
        from storages.db import init_db
        await init_db()
        logger.info("✅ База данных инициализирована")
        await dp.start_polling(bot)
    except ValueError as e:
        logger.critical(f"❌ Ошибка конфигурации: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"❌ Критическая ошибка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())