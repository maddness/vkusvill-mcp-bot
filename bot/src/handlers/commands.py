"""Command handlers"""
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode

from ..agent.runner import AgentRunner

log = logging.getLogger(__name__)

router = Router()
agent_runner = AgentRunner()


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Handle /start command"""
    thread_id = message.message_thread_id or 0
    agent_runner.reset_session(message.from_user.id, thread_id)
    await message.answer(
        "Привет! Я помогу собрать корзину продуктов ВкусВилл\\.\n\n"
        "Напиши что хочешь приготовить или какие продукты нужны\\.\n\n"
        "💡 *Команда:*\n"
        "/new\\_chat \\- Сбросить контекст\n\n"
        "📝 Храню последние 20 сообщений \\(10 пар запрос\\-ответ\\)",
        parse_mode=ParseMode.MARKDOWN_V2
    )


@router.message(Command("new_chat"))
async def cmd_new_chat(message: Message):
    """Handle /new_chat command"""
    thread_id = message.message_thread_id or 0
    agent_runner.reset_session(message.from_user.id, thread_id)
    await message.answer("Контекст сброшен. Начинаем заново!")


@router.message(Command("new_topic"))
async def cmd_new_topic(message: Message):
    """Handle /new_topic command (Bot API 9.3)"""
    from aiogram import Bot
    bot: Bot = message.bot
    
    try:
        args = message.text.split(maxsplit=1)
        topic_name = args[1] if len(args) > 1 else "Новая корзина"
        
        result = await bot.create_forum_topic(
            chat_id=message.chat.id,
            name=topic_name,
            icon_color=0x6FB9F0,
            icon_custom_emoji_id=None
        )
        
        await bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=result.message_thread_id,
            text=f"📝 Тема *{topic_name}* создана!\n\nЧто будем готовить?",
            parse_mode=ParseMode.MARKDOWN
        )
        
        log.info(f"✅ Создан топик '{topic_name}' для пользователя {message.from_user.id}")
        
    except Exception as e:
        error_msg = str(e)
        log.error(f"❌ Ошибка создания топика: {error_msg}")
        
        if "chat is not a forum" in error_msg:
            await message.answer(
                "⚠️ Топики не включены для этого бота.\n\n"
                "📝 *Владелец бота должен включить топики через @BotFather:*\n"
                "1. Открыть чат с @BotFather\n"
                "2. /mybots → выбрать бота\n"
                "3. Bot Settings → Topics in Private Chats\n"
                "4. Включить опцию\n\n"
                "Это новая функция Bot API 9.3 (31 декабря 2025).\n\n"
                "Пока что используйте `/new_chat` для сброса контекста.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await message.answer(
                f"⚠️ Не удалось создать тему: {error_msg}\n\n"
                "Попробуйте включить режим топиков в настройках чата с ботом."
            )


@router.callback_query(F.data == "new_basket")
async def callback_new_basket(callback: CallbackQuery):
    """Handle new basket callback"""
    thread_id = callback.message.message_thread_id or 0
    agent_runner.reset_session(callback.from_user.id, thread_id)
    await callback.answer()
    await callback.message.answer("Начинаем собирать новую корзину! Что приготовим?")


