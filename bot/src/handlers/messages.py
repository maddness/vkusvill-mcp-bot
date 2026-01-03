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
from ..utils.transcriber import VoiceTranscriber

log = logging.getLogger(__name__)

router = Router()
agent_runner = AgentRunner()
user_locks: dict[int, asyncio.Lock] = {}

# Инициализируем логгер, БД и транскрибер
agent_logger = AgentLogger()
user_db = UserDatabase()

# Инициализируем транскрибер если настроен
transcriber = None
if config.whisper_api_url:
    transcriber = VoiceTranscriber(
        api_url=config.whisper_api_url,
        api_key=config.whisper_api_key,
        model=config.whisper_model,
        max_file_size_mb=config.whisper_max_file_size_mb,
        max_duration_seconds=config.whisper_max_duration_seconds
    )


def get_user_lock(user_id: int) -> asyncio.Lock:
    """Get or create lock for user"""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


async def notify_admins(bot, message: Message, response: str = None, transcribed_text: str = None):
    """Notify admins about user request"""
    user_info = f"👤 {message.from_user.full_name}"
    if message.from_user.username:
        user_info += f" (@{message.from_user.username})"
    user_info += f" [ID: {message.from_user.id}]"
    
    for admin_id in config.admin_ids:
        # Для групп (отрицательные ID) всегда отправляем
        # Для личных чатов (положительные ID) не отправляем самому себе
        if admin_id < 0 or admin_id != message.from_user.id:
            try:
                # Отправляем информацию о пользователе
                await bot.send_message(admin_id, f"📨 Новый запрос:\n{user_info}")
                
                # Пересылаем оригинальное сообщение (текст или голосовое)
                await bot.forward_message(
                    chat_id=admin_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id
                )
                
                # Если это голосовое сообщение, отправляем распознанный текст
                if transcribed_text:
                    await bot.send_message(admin_id, f"📝 Распознано: {transcribed_text}")
                
                # Если есть ответ бота, отправляем его
                if response:
                    response_text = f"🤖 Ответ бота:\n{response[:500]}"
                    if len(response) > 500:
                        response_text += "..."
                    await bot.send_message(admin_id, response_text)
            except Exception as e:
                log.error(f"❌ Не удалось отправить уведомление в чат {admin_id}: {e}")


@router.message(F.text)
async def handle_message(message: Message):
    """Handle text messages"""
    # В админ-группе реагируем только на сообщения, начинающиеся с "вкусик"
    if message.chat.id in config.admin_ids:
        if not message.text.lower().startswith("вкусик"):
            return
        # Убираем "вкусик" из текста
        message.text = message.text[6:].strip()
        if not message.text:
            await message.answer("Чем могу помочь?")
            return
    
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


@router.message(F.voice)
async def handle_voice(message: Message):
    """Handle voice messages"""
    # В админ-группе игнорируем голосовые сообщения
    if message.chat.id in config.admin_ids:
        return
    
    if not transcriber:
        await message.answer("⚠️ Транскрибация голосовых сообщений не настроена")
        return
    
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
        # Check file size
        file_size_mb = message.voice.file_size / (1024 * 1024)
        if file_size_mb > config.whisper_max_file_size_mb:
            await message.answer(
                f"⚠️ Голосовое сообщение слишком большое: {file_size_mb:.1f} MB\n"
                f"Максимальный размер: {config.whisper_max_file_size_mb} MB"
            )
            return
        
        # Check duration
        if message.voice.duration > config.whisper_max_duration_seconds:
            await message.answer(
                f"⚠️ Голосовое сообщение слишком длинное: {message.voice.duration}с\n"
                f"Максимальная длительность: {config.whisper_max_duration_seconds}с (3 минуты)"
            )
            return
        
        status_msg = await message.answer("🎤 Транскрибирую голосовое сообщение...")
        
        try:
            # Download voice message
            file = await message.bot.get_file(message.voice.file_id)
            audio_bytes = await message.bot.download_file(file.file_path)
            
            # Transcribe
            text = await transcriber.transcribe(
                audio_file=audio_bytes.read(),
                filename=f"voice_{message.voice.file_id}.ogg"
            )
            
            if not text:
                await status_msg.edit_text("❌ Не удалось распознать голосовое сообщение")
                return
            
            # Delete status message
            await status_msg.delete()
            
            # Show transcribed text and send it as new message for processing
            transcribed_msg = await message.answer(f"📝 Распознано: _{text}_", parse_mode=ParseMode.MARKDOWN)
            
            # Now process the transcribed text through the agent
            # We need to handle it in the same context but as a text message
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
                response = await agent_runner.run(user_id, username, text, send_progress, stream_text, thread_id)
                
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
                    query=f"[VOICE] {text}",
                    response=response,
                    tools_used=tools_used,
                    tokens=tokens_info
                )
                
                # Notify admins with transcribed text
                await notify_admins(message.bot, message, response, transcribed_text=text)
            
            except Exception as agent_error:
                error_text = str(agent_error)
                log.error(f"❌ Ошибка обработки агентом: {error_text}")
                
                # Логируем ошибку
                agent_logger.log_interaction(
                    user_id=user_id,
                    username=message.from_user.username or message.from_user.full_name,
                    query=f"[VOICE] {text}",
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
                        await stream_msg.edit_text(f"Произошла ошибка: {agent_error}")
                    except:
                        await message.answer(f"Произошла ошибка: {agent_error}")
                else:
                    await message.answer(f"Произошла ошибка: {agent_error}")
        
        except Exception as e:
            log.error(f"❌ Ошибка обработки голосового сообщения: {e}")
            try:
                await status_msg.edit_text(f"❌ Произошла ошибка: {e}")
            except:
                await message.answer(f"❌ Произошла ошибка: {e}")

