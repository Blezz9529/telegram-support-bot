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

# ✅ КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: настройка сессии с таймаутами
session = AiohttpSession(
    timeout=30.0,  # общий таймаут
    connection_timeout=10.0,  # таймаут подключения
    request_timeout=30.0     # таймаут запроса
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
    """Инициализация при запуске"""
    logger.info("🚀 Инициализация бота...")
    
    # Инициализация БД
    try:
        from storages.db import init_db
        await init_db()
        logger.info("✅ База данных готова")
    except Exception as e:
        logger.critical(f"❌ Ошибка БД: {e}")
        sys.exit(1)
    
    # Проверка доступа к форуму
    try:
        chat = await bot.get_chat(SUPPORT_GROUP_ID)
        logger.info(f"✅ Форум подключён: {chat.title}")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к форуму: {e}")
        sys.exit(1)


async def main():
    await on_startup()
    logger.info("📡 Бот запущен и ожидает сообщений...")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "chat_member"],
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
            pass  # Игнорируем для Windows
        else:
            raise