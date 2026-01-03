"""VkusVill Bot Application"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from agents import set_default_openai_api, set_tracing_disabled
import litellm

from bot.src.handlers import commands, messages
from bot.src.utils.config import config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# Disable LiteLLM debug logs
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

# Configure environment
os.environ["SSL_VERIFY"] = "false"
os.environ["OPENAI_API_BASE"] = config.llm_api_base
os.environ["OPENAI_API_KEY"] = config.llm_api_key

# Configure agents
set_default_openai_api("chat_completions")
set_tracing_disabled(True)
litellm.drop_params = True


async def on_startup(bot: Bot):
    """Bot startup handler"""
    log.info(f"🤖 Модель: {config.llm_model}")
    log.info("🚀 Бот запущен")
    
    # Notify admins
    startup_message = (
        "🚀 *Бот VkusVill AI запущен!*\n\n"
        "✅ Система готова к работе\n"
        f"🤖 Модель: {config.llm_model.split('/')[-1]}\n"
        "⚡ Стриминг ответов активирован"
    )
    
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(admin_id, startup_message, parse_mode=ParseMode.MARKDOWN)
            log.info(f"✅ Уведомление о старте отправлено админу {admin_id}")
        except Exception as e:
            log.error(f"❌ Не удалось отправить уведомление о старте админу {admin_id}: {e}")


async def main():
    """Main application entry point"""
    # Initialize bot and dispatcher
    bot = Bot(token=config.telegram_bot_token)
    dp = Dispatcher()
    
    # Register handlers
    dp.include_router(commands.router)
    dp.include_router(messages.router)
    
    # Register startup handler
    dp.startup.register(on_startup)
    
    # Start polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

