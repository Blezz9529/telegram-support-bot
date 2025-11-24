# main.py (финальная, проверенная версия)
import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from config import BOT_TOKEN, SUPPORT_GROUP_ID

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ✅ ПРОСТОЙ И РАБОЧИЙ ВАРИАНТ: без кастомной сессии
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)
dp = Dispatcher()

try:
    from handlers import user, admin
    dp.include_router(user.router)
    dp.include_router(admin.router)
except ImportError as e:
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
        logger.critical(f"❌ Ошибка: {e}")
        sys.exit(1)


async def main():
    await on_startup()
    logger.info("📡 Бот запущен...")
    try:
        # ✅ НАСТРОЙКА ТАЙМАУТОВ В start_polling()
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query"],
            # Защита от Flood Control:
            polling_timeout=30,  # таймаут опроса
            handle_asynchronously=True,  # асинхронная обработка
            # Ключевое: request_timeout передаётся отдельно
            bot_request_timeout=30.0  # ← РАБОЧИЙ ПАРАМЕТР
        )
    except KeyboardInterrupt:
        logger.info("🛑 Остановка")
    except Exception as e:
        logger.exception(f"💥 Ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "event loop is closed" in str(e):
            pass
        else:
            raise