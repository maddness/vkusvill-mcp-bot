"""Command handlers"""
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode

from .messages import agent_runner, user_db
from ..utils.config import config

log = logging.getLogger(__name__)

router = Router()

# Список ID админов
ADMIN_IDS = [568519460, 809532582]


async def safe_send_message(bot_or_message, chat_id: int, text: str, **kwargs):
    """Send message with Markdown fallback"""
    try:
        if hasattr(bot_or_message, 'send_message'):
            # It's a bot object
            await bot_or_message.send_message(chat_id, text, **kwargs)
        else:
            # It's a message object
            await bot_or_message.answer(text, **kwargs)
    except Exception as e:
        # If Markdown parsing fails, try without it
        if 'parse_mode' in kwargs:
            try:
                kwargs_copy = kwargs.copy()
                kwargs_copy.pop('parse_mode', None)
                plain_text = text.replace('`', '').replace('*', '').replace('_', '')
                if hasattr(bot_or_message, 'send_message'):
                    await bot_or_message.send_message(chat_id, plain_text, **kwargs_copy)
                else:
                    await bot_or_message.answer(plain_text, **kwargs_copy)
                log.warning(f"⚠️ Отправлено без Markdown (ошибка парсинга): {str(e)[:100]}")
            except Exception as e2:
                log.error(f"❌ Не удалось отправить сообщение: {e2}")
                raise
        else:
            raise


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Handle /start command"""
    thread_id = message.message_thread_id or 0
    agent_runner.reset_session(message.from_user.id, thread_id)
    await message.answer(
        "Привет\\! Я помогу собрать корзину продуктов ВкусВилл\\.\n\n"
        "Напиши что хочешь приготовить или какие продукты нужны\\.\n\n"
        "💡 *Команда:*\n"
        "/new\\_chat \\- Сбросить контекст\n\n"
        "📝 Храню последние 20 сообщений \\(10 пар запрос\\-ответ\\)",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    # Уведомляем админов о новом старте
    user = message.from_user
    user_info = f"👤 {user.full_name}"
    if user.username:
        user_info += f" (@{user.username})"
    user_info += f"\nID: `{user.id}`"
    
    # Проверяем, новый ли это пользователь
    user_data = user_db.get_user(user.id)
    is_new = user_data is None or user_data.get("total_interactions", 0) == 0
    
    notification = f"🆕 Новый пользователь!" if is_new else "🔄 Пользователь запустил /start"
    notification += f"\n\n{user_info}"
    
    for admin_id in config.admin_ids:
        try:
            await safe_send_message(message.bot, admin_id, notification, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            log.error(f"❌ Не удалось отправить уведомление админу {admin_id}: {e}")


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


@router.message(Command("ban"))
async def cmd_ban(message: Message):
    """Handle /ban command (admin only)"""
    # Проверяем, является ли пользователь админом
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет прав для выполнения этой команды.")
        return
    
    # Парсим ID пользователя из команды
    try:
        args = message.text.split(maxsplit=2)
        if len(args) < 2:
            await message.answer("⚠️ Использование: /ban <user_id> [причина]")
            return
        
        user_id_to_ban = int(args[1])
        reason = args[2] if len(args) > 2 else "Нарушение правил"
        
        # Проверяем, не пытается ли админ забанить себя или другого админа
        if user_id_to_ban in ADMIN_IDS:
            await message.answer("⛔ Нельзя забанить администратора.")
            return
        
        # Баним пользователя
        user_db.ban_user(user_id_to_ban, message.from_user.id, reason)
        
        # Получаем информацию о пользователе
        user_info = user_db.get_user(user_id_to_ban)
        username = user_info.get("username", "неизвестно") if user_info else "неизвестно"
        
        await message.answer(
            f"🚫 Пользователь забанен\n\n"
            f"ID: `{user_id_to_ban}`\n"
            f"Username: @{username}\n"
            f"Причина: {reason}",
            parse_mode=ParseMode.MARKDOWN
        )
        
        log.info(f"🚫 Админ {message.from_user.id} забанил пользователя {user_id_to_ban}. Причина: {reason}")
        
    except ValueError:
        await message.answer("⚠️ Неверный формат ID пользователя. Используйте числа.")
    except Exception as e:
        log.error(f"❌ Ошибка при бане пользователя: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("unban"))
async def cmd_unban(message: Message):
    """Handle /unban command (admin only)"""
    # Проверяем, является ли пользователь админом
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет прав для выполнения этой команды.")
        return
    
    # Парсим ID пользователя из команды
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer("⚠️ Использование: /unban <user_id>")
            return
        
        user_id_to_unban = int(args[1])
        
        # Разбаниваем пользователя
        if user_db.unban_user(user_id_to_unban):
            await message.answer(f"✅ Пользователь `{user_id_to_unban}` разбанен.", parse_mode=ParseMode.MARKDOWN)
            log.info(f"✅ Админ {message.from_user.id} разбанил пользователя {user_id_to_unban}")
        else:
            await message.answer(f"⚠️ Пользователь `{user_id_to_unban}` не был забанен.", parse_mode=ParseMode.MARKDOWN)
        
    except ValueError:
        await message.answer("⚠️ Неверный формат ID пользователя. Используйте числа.")
    except Exception as e:
        log.error(f"❌ Ошибка при разбане пользователя: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("banned"))
async def cmd_banned(message: Message):
    """Handle /banned command - show list of banned users (admin only)"""
    # Проверяем, является ли пользователь админом
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет прав для выполнения этой команды.")
        return
    
    try:
        banned_users = user_db.get_banned_users()
        
        if not banned_users:
            await message.answer("✅ Нет забаненных пользователей.")
            return
        
        response = "🚫 *Забаненные пользователи:*\n\n"
        
        for banned in banned_users:
            user_id = banned["user_id"]
            reason = banned.get("reason", "Не указана")
            banned_at = banned.get("banned_at", "Неизвестно")
            
            # Получаем информацию о пользователе
            user_info = user_db.get_user(user_id)
            username = user_info.get("username", "неизвестно") if user_info else "неизвестно"
            
            response += f"• ID: `{user_id}` (@{username})\n"
            response += f"  Причина: {reason}\n"
            response += f"  Дата: {banned_at[:10]}\n\n"
        
        await message.answer(response, parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        log.error(f"❌ Ошибка при получении списка забаненных: {e}")
        await message.answer(f"❌ Ошибка: {e}")



