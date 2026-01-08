"""VkusVill Bot Application"""
import asyncio
import logging
import os
import subprocess

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
litellm.drop_params = True

# Configure Langfuse tracing via OpenTelemetry
if config.langfuse_enabled:
    import base64
    from opentelemetry import trace, baggage
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

    # BaggageSpanProcessor to propagate baggage items as span attributes
    class BaggageSpanProcessor(SpanProcessor):
        def on_start(self, span, parent_context):
            for key, value in baggage.get_all(parent_context).items():
                span.set_attribute(key, value)

        def on_end(self, span):
            pass

        def shutdown(self):
            pass

        def force_flush(self, timeout_millis=30000):
            return True

    # Setup OTLP exporter for Langfuse
    auth_string = base64.b64encode(
        f"{config.langfuse_public_key}:{config.langfuse_secret_key}".encode()
    ).decode()

    langfuse_endpoint = f"{config.langfuse_base_url}/api/public/otel/v1/traces"

    exporter = OTLPSpanExporter(
        endpoint=langfuse_endpoint,
        headers={"Authorization": f"Basic {auth_string}"}
    )

    # Setup TracerProvider with BaggageSpanProcessor
    provider = TracerProvider()
    provider.add_span_processor(BaggageSpanProcessor())
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Instrument OpenAI Agents SDK
    OpenAIAgentsInstrumentor().instrument()
    log.info(f"Langfuse tracing enabled (endpoint: {langfuse_endpoint})")
else:
    set_tracing_disabled(True)
    log.info("Langfuse not configured, tracing disabled")


def get_git_info() -> tuple[str, str, str]:
    """
    Получаем информацию о текущем git коммите.
    
    Returns:
        tuple: (commit_hash, commit_date, branch)
    """
    try:
        commit_hash = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            stderr=subprocess.DEVNULL
        ).decode('utf-8').strip()
        
        commit_date = subprocess.check_output(
            ['git', 'log', '-1', '--format=%cd', '--date=format:%Y-%m-%d %H:%M'],
            stderr=subprocess.DEVNULL
        ).decode('utf-8').strip()
        
        branch = subprocess.check_output(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            stderr=subprocess.DEVNULL
        ).decode('utf-8').strip()
        
        return commit_hash, commit_date, branch
    except Exception:
        return "unknown", "unknown", "unknown"


async def on_startup(bot: Bot):
    """Bot startup handler"""
    # Получаем информацию о коммите
    commit_hash, commit_date, branch = get_git_info()
    
    log.info(f"🤖 Модель: {config.llm_model}")
    log.info(f"📝 Коммит: {commit_hash} ({branch})")
    log.info(f"📅 Дата: {commit_date}")
    log.info("🚀 Бот запущен")
    
    # Notify admins
    startup_message = (
        "🚀 *Бот VkusVill AI запущен!*\n\n"
        "✅ Система готова к работе\n"
        f"🤖 Модель: {config.llm_model.split('/')[-1]}\n"
        "⚡ Стриминг ответов активирован\n\n"
        f"📝 Коммит: `{commit_hash}` ({branch})\n"
        f"📅 {commit_date}"
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

