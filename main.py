# main.py
import asyncio
import logging
import sys
import os
from aiogram import Dispatcher
from config import BOT_TOKEN, SUPPORT_GROUP_ID

# Исправление для uvloop на macOS
asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

# Настройка логгера
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ✅ Добавляем google в sys.path, если используется локальный __init__.py
if os.path.exists("google/__init__.py"):
    sys.path.insert(0, ".")

from services.bot_context import get_bot

bot = get_bot()
dp = Dispatcher()

# Импорт хэндлеров
try:
    from handlers import user, admin, widget
    dp.include_router(user.router)
    dp.include_router(admin.router)
    # Router виджета не нужен — это REST API + WebSocket
    logger.info("✅ Хэндлеры загружены (user, admin, widget)")
except Exception as e:
    logger.critical(f"❌ Ошибка импорта handlers: {e}")
    sys.exit(1)


async def on_startup():
    logger.info("🚀 Инициализация бота...")
    try:
        # Закрываем любую активную сессию getUpdates
        await bot.get_updates(offset=-1, timeout=1)
        logger.info("✅ Сессия getUpdates закрыта")

        # Удаляем webhook (на случай если был настроен ранее)
        await bot.delete_webhook()
        logger.info("✅ Webhook удалён")

        from storages.db import init_db
        from services.widget_session import init_widget_db
        from services.site_user_map import init_site_user_map
        from services.conversation_store import init_conversation_store
        await init_db()
        await init_widget_db()
        await init_site_user_map()
        await init_conversation_store()
        logger.info("✅ База данных готова (основная + виджет)")
        
        # 🔑 Автоматический бэкап при старте
        try:
            from storages.db import backup_database
            await backup_database()
            logger.info("💾 Автоматический бэкап создан")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось создать бэкап: {e}")

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
