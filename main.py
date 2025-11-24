# main.py
import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from config import BOT_TOKEN, SUPPORT_GROUP_ID

# Настройка логгера
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ✅ ПРАВИЛЬНАЯ НАСТРОЙКА СЕССИИ ДЛЯ aiohttp + aiogram ≥3.13
import aiohttp
session = AiohttpSession(
    # Единый таймаут для всех операций
    timeout=aiohttp.ClientTimeout(
        total=30.0,      # общий лимит на запрос
        connect=10.0,    # таймаут подключения
        sock_read=20.0,  # таймаут чтения
        sock_connect=10.0  # таймаут установки соединения
    )
)

bot = Bot(
    token=BOT_TOKEN,
    session=session,  # ← ИСПОЛЬЗУЕМ НАСТРОЕННУЮ СЕССИЮ
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
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query"]
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