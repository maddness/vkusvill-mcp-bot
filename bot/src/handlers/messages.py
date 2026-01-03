"""Message handlers"""
import time
import logging
import asyncio
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

from ..agent.runner import AgentRunner
from ..utils.config import config
from ..utils.logger import AgentLogger
from ..utils.database import UserDatabase

log = logging.getLogger(__name__)

router = Router()
agent_runner = AgentRunner()
user_locks: dict[int, asyncio.Lock] = {}

# Инициализируем логгер и БД
agent_logger = AgentLogger()
user_db = UserDatabase()


def get_user_lock(user_id: int) -> asyncio.Lock:
    """Get or create lock for user"""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


async def notify_admins(bot, message: Message, response: str = None):
    """Notify admins about user request"""
    user_info = f"👤 {message.from_user.full_name}"
    if message.from_user.username:
        user_info += f" (@{message.from_user.username})"
    user_info += f" [ID: {message.from_user.id}]"
    
    notification = f"📨 Новый запрос:\n{user_info}\n\n💬 Сообщение: {message.text}"
    
    if response:
        notification += f"\n\n🤖 Ответ бота:\n{response[:500]}"
        if len(response) > 500:
            notification += "..."
    
    for admin_id in config.admin_ids:
        if admin_id != message.from_user.id:
            try:
                await bot.send_message(admin_id, notification)
            except Exception as e:
                log.error(f"❌ Не удалось отправить уведомление админу {admin_id}: {e}")


@router.message(F.text)
async def handle_message(message: Message):
    """Handle text messages"""
    user_id = message.from_user.id
    lock = get_user_lock(user_id)
    
    # Регистрируем пользователя в БД
    user_db.add_user(
        user_id=user_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )
    user_db.log_interaction(user_id)
    
    if lock.locked():
        await message.answer("⏳ Подожди, обрабатываю предыдущий запрос...")
        return
    
    async with lock:
        progress_msg = None
        stream_msg = None
        is_streaming = False
        tools_used = []
        tokens_info = None
        error_text = None
        
        async def send_progress(text: str):
            nonlocal progress_msg
            if progress_msg:
                try:
                    await progress_msg.edit_text(text)
                except:
                    pass
            else:
                progress_msg = await message.answer(text)
        
        async def stream_text(text: str):
            """Stream text updates"""
            nonlocal stream_msg, is_streaming, progress_msg
            
            if not is_streaming and progress_msg:
                try:
                    await progress_msg.delete()
                    progress_msg = None
                except:
                    pass
            
            display_text = text
            if "<think>" in display_text:
                think_end = display_text.find("</think>")
                if think_end > 0:
                    display_text = display_text[think_end+8:].strip()
            
            if not display_text:
                return
            
            try:
                thread_id = message.message_thread_id
                
                # Try sendMessageDraft (Bot API 9.3)
                result = await message.bot.session.post(
                    f"{message.bot.session.api.base}/bot{message.bot.token}/sendMessageDraft",
                    json={
                        "chat_id": message.chat.id,
                        "text": display_text + " ▌",
                        "parse_mode": "Markdown",
                        "message_thread_id": thread_id if thread_id else None,
                        "draft_message_id": stream_msg.message_id if stream_msg else None
                    }
                )
                
                if result.status == 200:
                    data = await result.json()
                    if data.get("ok") and not stream_msg:
                        from aiogram.types import Message as TgMessage
                        stream_msg = TgMessage(**data["result"])
                        is_streaming = True
            
            except Exception as e:
                # Fallback to editMessageText with rate limiting
                log.debug(f"sendMessageDraft не поддерживается, используем editMessageText: {e}")
                try:
                    if not stream_msg:
                        stream_msg = await message.answer(display_text + " ▌", parse_mode=ParseMode.MARKDOWN)
                        is_streaming = True
                    else:
                        current_time = time.time()
                        if not hasattr(stream_text, 'last_update') or current_time - stream_text.last_update >= 1.0:
                            await stream_msg.edit_text(display_text + " ▌", parse_mode=ParseMode.MARKDOWN)
                            stream_text.last_update = current_time
                except Exception as edit_error:
                    if "Flood control" not in str(edit_error):
                        log.error(f"Ошибка обновления сообщения: {edit_error}")
        
        await send_progress("💭 Думаю...")
        
        try:
            username = message.from_user.username or message.from_user.full_name
            thread_id = message.message_thread_id or 0
            response = await agent_runner.run(user_id, username, message.text, send_progress, stream_text, thread_id)
            
            # Получаем информацию о токенах и использованных инструментах
            session_key = f"{user_id}:{thread_id}"
            if session_key in agent_runner.sessions:
                session = agent_runner.sessions[session_key]
                if hasattr(session, 'last_tokens'):
                    tokens_info = session.last_tokens
                if hasattr(session, 'tools_used'):
                    tools_used = session.tools_used
            
            if progress_msg:
                try:
                    await progress_msg.delete()
                except:
                    pass
                progress_msg = None
            
            keyboard = None
            if "vkusvill.ru" in response:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🛒 Собрать новую корзину", callback_data="new_basket")]
                ])
            
            # Final message
            if stream_msg:
                try:
                    await stream_msg.edit_text(response, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
                except:
                    await stream_msg.edit_text(response, reply_markup=keyboard)
            else:
                try:
                    await message.answer(response, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
                except:
                    await message.answer(response, reply_markup=keyboard)
            
            # Логируем взаимодействие
            agent_logger.log_interaction(
                user_id=user_id,
                username=username,
                query=message.text,
                response=response,
                tools_used=tools_used,
                tokens=tokens_info
            )
            
            # Notify admins
            await notify_admins(message.bot, message, response)
        
        except Exception as e:
            error_text = str(e)
            log.error(f"❌ Ошибка обработки сообщения: {error_text}")
            
            # Логируем ошибку
            agent_logger.log_interaction(
                user_id=user_id,
                username=message.from_user.username or message.from_user.full_name,
                query=message.text,
                response="",
                error=error_text
            )
            
            if progress_msg:
                try:
                    await progress_msg.delete()
                except:
                    pass
            if stream_msg:
                try:
                    await stream_msg.edit_text(f"Произошла ошибка: {e}")
                except:
                    await message.answer(f"Произошла ошибка: {e}")
            else:
                await message.answer(f"Произошла ошибка: {e}")

