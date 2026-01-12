"""Message handlers"""
import re
import time
import logging
import asyncio
import base64
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, URLInputFile
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


def extract_vkusvill_image(text: str) -> tuple[str | None, str]:
    """Extract VkusVill image URL from text and return (image_url, cleaned_text)"""
    # Pattern for VkusVill image URLs
    pattern = r'https://img\.vkusvill\.ru/[^\s\)\]]+\.webp[^\s\)\]]*'
    match = re.search(pattern, text)

    if match:
        image_url = match.group(0)
        # Remove markdown image link if present: [text](url) or (url)
        cleaned = re.sub(r'\[📷[^\]]*\]\([^\)]+\)\s*', '', text)
        cleaned = re.sub(r'\(https://img\.vkusvill\.ru/[^\)]+\)\s*', '', cleaned)
        # Also remove standalone URL
        cleaned = re.sub(pattern + r'\s*', '', cleaned)
        return image_url, cleaned.strip()

    return None, text


def clean_technical_output(text: str) -> str:
    """Remove technical details like function_calls from agent output"""
    # Remove <function_calls>...</function_calls> blocks
    text = re.sub(r'<function_calls>.*?</function_calls>', '', text, flags=re.DOTALL)
    
    # Remove blocks that start with [ and contain tool_name
    text = re.sub(r'\[[\s\S]*?"tool_name"[\s\S]*?\]', '', text, flags=re.MULTILINE)
    
    # Remove lines with technical JSON-like structures
    lines = text.split('\n')
    cleaned_lines = []
    in_technical_block = False
    bracket_count = 0
    
    for line in lines:
        stripped = line.strip()
        
        # Detect start of technical block
        if '"tool_name"' in line or '"arguments"' in line or ('{"query"' in line and '"tool_name"' in text):
            in_technical_block = True
            bracket_count = line.count('{') + line.count('[')
            bracket_count -= line.count('}') + line.count(']')
            continue
        
        # Track brackets in technical block
        if in_technical_block:
            bracket_count += line.count('{') + line.count('[')
            bracket_count -= line.count('}') + line.count(']')
            if bracket_count <= 0:
                in_technical_block = False
            continue
        
        # Skip lines that look technical
        if any(x in line for x in ['"tool_name":', '"arguments":', '{"query":', '"search_products"', '"create_cart"']):
            continue
        
        # Skip empty brackets/braces lines
        if stripped in ['[', ']', '{', '}', '[{', '}]']:
            continue
        
        # Skip "Ищу товары..." or "Собираю корзину..." technical phrases
        if stripped.startswith('Ищу товары') or stripped.startswith('Собираю корзину'):
            if '<function_calls>' in text or 'tool_name' in text:
                continue
        
        cleaned_lines.append(line)
    
    # Remove multiple consecutive empty lines
    result = '\n'.join(cleaned_lines)
    result = re.sub(r'\n{3,}', '\n\n', result)
    
    return result.strip()


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
                    # Telegram лимит - 4096 символов, оставляем место для заголовка
                    max_length = 4000
                    response_text = f"🤖 Ответ бота:\n{response[:max_length]}"
                    if len(response) > max_length:
                        response_text += "\n\n... (обрезано)"
                    
                    try:
                        await bot.send_message(admin_id, response_text, parse_mode=ParseMode.MARKDOWN)
                    except:
                        # Если не удалось с Markdown, отправляем без форматирования
                        await bot.send_message(admin_id, response_text)
            except Exception as e:
                log.error(f"❌ Не удалось отправить уведомление в чат {admin_id}: {e}")


@router.message(F.text)
async def handle_message(message: Message):
    """Handle text messages"""
    # В админ-группе реагируем только на сообщения, начинающиеся с "вкусик"
    user_message = message.text
    if message.chat.id in config.admin_ids:
        if not user_message.lower().startswith("вкусик"):
            return
        # Убираем "вкусик" из текста
        user_message = user_message[6:].strip()
        if not user_message:
            await message.answer("Чем могу помочь?")
            return
    
    user_id = message.from_user.id
    
    # Проверяем, не забанен ли пользователь
    if user_db.is_banned(user_id):
        await message.answer("⛔ Извините, вам ограничен доступ к боту.")
        log.warning(f"🚫 Попытка доступа забаненного пользователя {user_id}")
        return
    
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
                        stream_msg = await message.answer(display_text + " ▌")
                        is_streaming = True
                    else:
                        current_time = time.time()
                        if not hasattr(stream_text, 'last_update') or current_time - stream_text.last_update >= 1.0:
                            await stream_msg.edit_text(display_text + " ▌")
                            stream_text.last_update = current_time
                except Exception as edit_error:
                    if "Flood control" not in str(edit_error):
                        log.error(f"Ошибка обновления сообщения: {edit_error}")
        
        await send_progress("💭 Думаю...")
        
        try:
            username = message.from_user.username or message.from_user.full_name
            thread_id = message.message_thread_id or 0
            response = await agent_runner.run(user_id, username, user_message, send_progress, stream_text, thread_id)

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

            # Clean technical output (remove function_calls, etc)
            response = clean_technical_output(response)
            
            # Check for VkusVill product image
            image_url, cleaned_response = extract_vkusvill_image(response)

            # Log cart state before sending response
            if session_key in agent_runner.sessions:
                cart = agent_runner.sessions[session_key].cart_products
                log.info(f"🛒 Корзина пользователя {user_id}: {len(cart)} товаров: {dict(cart)}")

            # Обрезаем слишком длинные ответы (Telegram лимит 4096 символов)
            MAX_MESSAGE_LENGTH = 4000  # Оставляем запас
            if len(response) > MAX_MESSAGE_LENGTH:
                log.warning(f"⚠️ Ответ слишком длинный ({len(response)} символов), обрезаем")
                response = response[:MAX_MESSAGE_LENGTH] + "\n\n... _(ответ обрезан, слишком длинный)_"
                cleaned_response = response if not image_url else cleaned_response[:MAX_MESSAGE_LENGTH]

            # Final message
            if image_url:
                # Send photo with caption
                if stream_msg:
                    try:
                        await stream_msg.delete()
                    except:
                        pass
                try:
                    photo = URLInputFile(image_url)
                    await message.answer_photo(
                        photo=photo,
                        caption=cleaned_response[:1024],  # Telegram caption limit
                        reply_markup=keyboard,
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as photo_err:
                    log.warning(f"Failed to send photo, falling back to text: {photo_err}")
                    try:
                        await message.answer(cleaned_response, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
                    except:
                        await message.answer(cleaned_response, reply_markup=keyboard)
            elif stream_msg:
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
                query=user_message,
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
                query=user_message,
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
    
    # Проверяем, не забанен ли пользователь
    if user_db.is_banned(user_id):
        await message.answer("⛔ Извините, вам ограничен доступ к боту.")
        log.warning(f"🚫 Попытка доступа забаненного пользователя {user_id}")
        return
    
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
                            stream_msg = await message.answer(display_text + " ▌")
                            is_streaming = True
                        else:
                            current_time = time.time()
                            if not hasattr(stream_text, 'last_update') or current_time - stream_text.last_update >= 1.0:
                                await stream_msg.edit_text(display_text + " ▌")
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

                # Clean technical output
                response = clean_technical_output(response)
                
                # Log cart state before sending response
                if session_key in agent_runner.sessions:
                    cart = agent_runner.sessions[session_key].cart_products
                    log.info(f"🛒 Корзина пользователя {user_id}: {len(cart)} товаров: {dict(cart)}")

                # Обрезаем слишком длинные ответы
                MAX_MESSAGE_LENGTH = 4000
                if len(response) > MAX_MESSAGE_LENGTH:
                    log.warning(f"⚠️ Ответ слишком длинный ({len(response)} символов), обрезаем")
                    response = response[:MAX_MESSAGE_LENGTH] + "\n\n... _(ответ обрезан, слишком длинный)_"

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


@router.message(F.photo)
async def handle_photo(message: Message):
    """Handle photo messages"""
    # В админ-группе игнорируем фото без триггера
    caption = message.caption or ""
    if message.chat.id in config.admin_ids:
        if not caption.lower().startswith("вкусик"):
            return
        # Убираем "вкусик" из подписи
        caption = caption[6:].strip()
    
    user_id = message.from_user.id
    
    # Проверяем, не забанен ли пользователь
    if user_db.is_banned(user_id):
        await message.answer("⛔ Извините, вам ограничен доступ к боту.")
        log.warning(f"🚫 Попытка доступа забаненного пользователя {user_id}")
        return
    
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
        status_msg = await message.answer("🖼️ Анализирую изображение...")
        
        try:
            # Скачиваем фото (берем самое большое разрешение)
            photo = message.photo[-1]
            file = await message.bot.get_file(photo.file_id)
            photo_bytes = await message.bot.download_file(file.file_path)
            
            # Кодируем в base64
            photo_b64 = base64.b64encode(photo_bytes.read()).decode()
            
            # Формируем запрос с изображением
            if not caption:
                user_prompt = "Что изображено на этой картинке? Если это продукты или блюдо, помоги собрать корзину с похожими товарами из ВкусВилл."
            else:
                user_prompt = caption
            
            await status_msg.delete()
            
            # Создаем progress и stream callbacks
            progress_msg = None
            stream_msg = None
            is_streaming = False
            tools_used = []
            tokens_info = None
            
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
                    if not stream_msg:
                        stream_msg = await message.answer(display_text + " ▌")
                        is_streaming = True
                    else:
                        current_time = time.time()
                        if not hasattr(stream_text, 'last_update') or current_time - stream_text.last_update >= 1.0:
                            await stream_msg.edit_text(display_text + " ▌")
                            stream_text.last_update = current_time
                except Exception as e:
                    if "Flood control" not in str(e):
                        log.error(f"Ошибка обновления сообщения: {e}")
            
            await send_progress("💭 Думаю...")
            
            username = message.from_user.username or message.from_user.full_name
            thread_id = message.message_thread_id or 0
            
            # Запускаем агента с изображением
            response = await agent_runner.run_with_image(
                user_id, username, user_prompt, photo_b64, 
                send_progress, stream_text, thread_id
            )
            
            # Получаем информацию о токенах
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

            # Clean technical output
            response = clean_technical_output(response)
            
            # Log cart state before sending response
            if session_key in agent_runner.sessions:
                cart = agent_runner.sessions[session_key].cart_products
                log.info(f"🛒 Корзина пользователя {user_id}: {len(cart)} товаров: {dict(cart)}")

            # Обрезаем слишком длинные ответы
            MAX_MESSAGE_LENGTH = 4000
            if len(response) > MAX_MESSAGE_LENGTH:
                log.warning(f"⚠️ Ответ слишком длинный ({len(response)} символов), обрезаем")
                response = response[:MAX_MESSAGE_LENGTH] + "\n\n... _(ответ обрезан, слишком длинный)_"

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
                query=f"[PHOTO] {user_prompt}",
                response=response,
                tools_used=tools_used,
                tokens=tokens_info
            )
            
            # Notify admins
            await notify_admins(message.bot, message, response)
        
        except Exception as e:
            log.error(f"❌ Ошибка обработки фото: {e}")
            try:
                await status_msg.edit_text(f"❌ Произошла ошибка: {e}")
            except:
                await message.answer(f"❌ Произошла ошибка: {e}")

