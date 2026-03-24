import logging
from typing import Optional

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN, TELEGRAM_PROXY_URL, TELEGRAM_PROXY_ENABLED

logger = logging.getLogger(__name__)

_bot: Optional[Bot] = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        session = None
        if TELEGRAM_PROXY_ENABLED and TELEGRAM_PROXY_URL:
            session = AiohttpSession(proxy=TELEGRAM_PROXY_URL)
        _bot = Bot(
            token=BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            session=session,
        )
        logger.info("✅ Bot initialized in bot_context")
    return _bot
