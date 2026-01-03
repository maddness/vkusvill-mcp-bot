import asyncio
import os
import logging
import httpx
import html
import time
from dotenv import load_dotenv

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# Отключаем DEBUG логи LiteLLM
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode

from agents import Agent, Runner, set_default_openai_api, set_tracing_disabled, function_tool, ModelSettings
import litellm

load_dotenv()

os.environ["SSL_VERIFY"] = "false"
litellm.drop_params = True  # Игнорируем неподдерживаемые параметры

# Конфиг модели
MODEL_NAME = os.environ.get("MODEL", "litellm/openai/claude-haiku-4-5")
API_BASE = os.environ.get("API_BASE", "https://openai-hub.neuraldeep.tech/v1")
API_KEY = os.environ.get("ANTHROPIC_API_KEY")

if API_BASE:
    os.environ["OPENAI_API_BASE"] = API_BASE
if API_KEY:
    os.environ["OPENAI_API_KEY"] = API_KEY

log.info(f"🤖 Модель: {MODEL_NAME}")

bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
dp = Dispatcher()
sessions: dict[tuple[int, int], list] = {}  # (user_id, thread_id) -> messages
user_locks: dict[int, asyncio.Lock] = {}

# Администраторы для уведомлений
ADMIN_USERNAMES = ["aostrikov", "VaKovaLskii"]
ADMIN_IDS = [568519460, 809532582]  # ID администраторов

MAX_HISTORY_MESSAGES = 20  # 10 пар запрос-ответ
MCP_URL = os.environ.get("MCP_URL", "https://mcp001.vkusvill.ru/mcp")

SYSTEM_PROMPT = """Ты помощник для сбора продуктовых корзин ВкусВилл.

АЛГОРИТМ РАБОТЫ (строго по шагам):
1. Получил запрос на рецепт → вызови search_products для КАЖДОГО ингредиента
2. Собрал все xml_id найденных товаров → СРАЗУ вызови create_cart
3. Получил ссылку от create_cart → выведи красивый ответ со ссылкой

⚠️ КРИТИЧЕСКИ ВАЖНО: После поиска товаров ТЫ ОБЯЗАН вызвать create_cart!
Без create_cart пользователь НЕ ПОЛУЧИТ ссылку на корзину — это провал задачи!

ПРАВИЛА:
- Знаешь рецепт — сразу ищи, не спрашивай
- Бери ПЕРВЫЙ товар из каждого поиска
- "Побогаче/добавить" — ищи НОВЫЕ ингредиенты
- ПОМНИ предпочтения пользователя на протяжении всего разговора!
- НИКОГДА не меняй ингредиенты/блюда без явного согласия пользователя
- Если хочешь предложить замену — СНАЧАЛА спроси, потом меняй

ПРИМЕРЫ РЕЦЕПТОВ (только для справки, НЕ предлагай их по умолчанию!):
Это лишь ориентиры для понимания структуры ингредиентов. Используй свои знания о кулинарии!
- Оливье: картофель, морковь, яйца, колбаса докторская, горошек, огурцы солёные, майонез, лук
- Цезарь: салат, куриная грудка, пармезан, сухарики, соус цезарь, помидоры черри
- Борщ: свёкла, капуста, картофель, морковь, лук, говядина, томатная паста

ФОРМАТ ОТВЕТА (используй эмодзи!):

🛒 *КОРЗИНА ДЛЯ [НАЗВАНИЕ]*

1. 🥔 Картофель — 1 кг — *47 ₽*
2. 🥕 Морковь — 1 кг — *48 ₽*
3. 🥚 Яйца — 10 шт — *89 ₽*

💰 *Итого: XXX ₽*

[🛍 Перейти в корзину](ССЫЛКА)

✨ Приятных покупок!

Формат: *жирный* для цен, эмодзи для каждого товара, [текст](url) для ссылок."""

set_default_openai_api("chat_completions")
set_tracing_disabled(True)


# MCP HTTP клиент
class MCPClient:
    def __init__(self, url: str):
        self.url = url
        self.session_id = None

    async def call(self, method: str, params: dict) -> dict:
        async with httpx.AsyncClient(verify=False, timeout=60) as client:
            headers = {
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            }
            if self.session_id:
                headers["mcp-session-id"] = self.session_id

            # Сначала инициализируем сессию если нужно
            if not self.session_id:
                init_resp = await client.post(
                    self.url,
                    json={"jsonrpc": "2.0", "id": 0, "method": "initialize",
                          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                     "clientInfo": {"name": "vkusvill-bot", "version": "1.0"}}},
                    headers=headers
                )
                if "mcp-session-id" in init_resp.headers:
                    self.session_id = init_resp.headers["mcp-session-id"]
                    headers["mcp-session-id"] = self.session_id
                    # Отправляем initialized notification
                    await client.post(
                        self.url,
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                        headers=headers
                    )

            response = await client.post(
                self.url,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": method, "arguments": params}},
                headers=headers
            )

            if "mcp-session-id" in response.headers:
                self.session_id = response.headers["mcp-session-id"]

            data = response.json()
            if "error" in data:
                # Сброс сессии и повтор с реинициализацией
                self.session_id = None
                headers.pop("mcp-session-id", None)

                init_resp = await client.post(
                    self.url,
                    json={"jsonrpc": "2.0", "id": 0, "method": "initialize",
                          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                     "clientInfo": {"name": "vkusvill-bot", "version": "1.0"}}},
                    headers=headers
                )
                if "mcp-session-id" in init_resp.headers:
                    self.session_id = init_resp.headers["mcp-session-id"]
                    headers["mcp-session-id"] = self.session_id
                    await client.post(
                        self.url,
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                        headers=headers
                    )

                response = await client.post(
                    self.url,
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": method, "arguments": params}},
                    headers=headers
                )
                if "mcp-session-id" in response.headers:
                    self.session_id = response.headers["mcp-session-id"]
                data = response.json()

            return data.get("result", {})


mcp = MCPClient(MCP_URL)


# Кастомные tools с фильтрацией данных
@function_tool
async def search_products(query: str) -> str:
    """Поиск товаров ВкусВилл по названию. Возвращает список товаров с xml_id, названием, ценой и рейтингом (rating)."""
    log.info(f"🔍 Поиск: {query}")
    result = await mcp.call("vkusvill_products_search", {"q": query})

    # Извлекаем контент
    content = result.get("content", [])
    if not content:
        return "Товары не найдены"

    text = content[0].get("text", "")
    if not text:
        return "Товары не найдены"

    # Парсим JSON и фильтруем только нужные поля
    import json
    try:
        data = json.loads(text)
        # Ответ имеет структуру {"ok": true, "data": {"items": [...]}}
        products = data.get("data", {}).get("items", [])
        if not products:
            products = data if isinstance(data, list) else []

        # Оставляем только: xml_id, name, price, rating (минимум для корзины)
        filtered = []
        for p in products[:2]:  # Берём только 2 товара для экономии токенов
            rating = p.get("rating", {})
            filtered.append({
                "xml_id": p.get("xml_id"),
                "name": p.get("name", "")[:50],  # Обрезаем название
                "price": p.get("price"),
                "rating": rating.get("average") if rating else None
            })
        log.info(f"✅ Найдено {len(filtered)} товаров")
        return json.dumps(filtered, ensure_ascii=False) if filtered else "Товары не найдены"
    except Exception as e:
        log.error(f"❌ Ошибка парсинга: {e}")
        return text[:500]  # Fallback


@function_tool
async def create_cart(products_json: str) -> str:
    """Создаёт ссылку на корзину ВкусВилл. products_json: JSON строка вида [{"xml_id": 123, "q": 1}, ...]"""
    import json
    try:
        products = json.loads(products_json)
    except:
        log.error("❌ Неверный JSON для корзины")
        return "Ошибка: неверный формат JSON"
    log.info(f"🛒 Создаю корзину: {len(products)} товаров")
    result = await mcp.call("vkusvill_cart_link_create", {"products": products})

    content = result.get("content", [])
    if content:
        return content[0].get("text", "Ошибка создания корзины")
    return "Ошибка создания корзины"


def get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


async def run_agent(user_id: int, username: str, user_message: str, send_progress, stream_callback=None, thread_id: int = 0) -> str:
    log.info(f"👤 {username} ({user_id}, топик: {thread_id}): {user_message}")

    session_key = (user_id, thread_id)
    if session_key not in sessions:
        sessions[session_key] = []

    sessions[session_key].append({"role": "user", "content": user_message})

    if len(sessions[session_key]) > MAX_HISTORY_MESSAGES:
        sessions[session_key] = sessions[session_key][-MAX_HISTORY_MESSAGES:]

    settings = ModelSettings(include_usage=True)

    agent = Agent(
        name="VkusVill Assistant",
        model=MODEL_NAME,
        instructions=SYSTEM_PROMPT,
        tools=[search_products, create_cart],
        model_settings=settings,
    )

    result = Runner.run_streamed(agent, sessions[session_key])

    # Отслеживаем вызовы инструментов
    async for event in result.stream_events():
        if event.type == "run_item_stream_event":
            item = event.item
            if hasattr(item, 'raw_item') and hasattr(item.raw_item, 'name'):
                tool_name = item.raw_item.name
                if "search" in tool_name:
                    await send_progress("🔍 Ищу товары...")
                elif "cart" in tool_name:
                    await send_progress("🛒 Собираю корзину...")

    final = result.final_output

    # Логируем raw output для отладки
    log.info(f"🔍 Raw output (первые 500 симв.): {repr(final[:500]) if final else 'empty'}")

    # Логируем использование токенов
    try:
        usage = result.context_wrapper.usage
        cache_info = ""
        if hasattr(usage, 'cache_creation_input_tokens') and usage.cache_creation_input_tokens:
            cache_info += f", cache_write={usage.cache_creation_input_tokens}"
        if hasattr(usage, 'cache_read_input_tokens') and usage.cache_read_input_tokens:
            cache_info += f", cache_read={usage.cache_read_input_tokens}"
        log.info(f"📊 Токены: input={usage.input_tokens}, output={usage.output_tokens}, total={usage.total_tokens}{cache_info}")
    except:
        pass

    # Логируем наличие thinking
    if "<think>" in final:
        think_end = final.find("</think>")
        if think_end > 0:
            think_content = final[final.find("<think>")+7:think_end]
            log.info(f"🧠 Thinking ({len(think_content)} симв.): {think_content[:200]}...")
            # Убираем thinking из финального ответа
            final = final[think_end+8:].strip()

    # Настоящий стриминг через LiteLLM если нужно
    if stream_callback and final:
        try:
            # Используем LiteLLM для стриминга финального ответа
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            messages.extend(sessions[session_key])
            
            accumulated = ""
            last_update_len = 0
            last_update_time = 0
            
            response = await litellm.acompletion(
                model=MODEL_NAME.replace("litellm/", ""),
                messages=messages,
                stream=True,
                api_base=API_BASE,
                api_key=API_KEY,
            )
            
            async for chunk in response:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if hasattr(delta, 'content') and delta.content:
                        # Декодируем HTML entities
                        content = html.unescape(delta.content)
                        accumulated += content
                        
                        current_time = time.time()
                        # Обновляем каждые 50 символов ИЛИ каждую секунду
                        if (len(accumulated) - last_update_len >= 50) or (current_time - last_update_time >= 1.0):
                            # Убираем thinking теги из стрима
                            display_text = accumulated
                            if "<think>" in display_text:
                                think_end = display_text.find("</think>")
                                if think_end > 0:
                                    display_text = display_text[think_end+8:].strip()
                            
                            if display_text:
                                await stream_callback(display_text)
                                last_update_len = len(accumulated)
                                last_update_time = current_time
            
            # Финальное обновление
            if accumulated:
                display_text = accumulated
                if "<think>" in display_text:
                    think_end = display_text.find("</think>")
                    if think_end > 0:
                        display_text = display_text[think_end+8:].strip()
                if display_text:
                    await stream_callback(display_text)
                    
        except Exception as e:
            log.error(f"❌ Ошибка стриминга: {e}")
            # Fallback - показываем готовый ответ
            if final:
                await stream_callback(final)

    sessions[session_key].append({"role": "assistant", "content": final})
    log.info(f"✅ Ответ готов ({len(final)} символов)")
    return final


@dp.message(Command("start"))
async def cmd_start(message: Message):
    thread_id = message.message_thread_id or 0
    session_key = (message.from_user.id, thread_id)
    sessions.pop(session_key, None)
    await message.answer(
        "Привет! Я помогу собрать корзину продуктов ВкусВилл.\n\n"
        "Напиши что хочешь приготовить или какие продукты нужны.\n\n"
        "💡 *Команда:*\n"
        "/new_chat - Сбросить контекст\n\n"
        "📝 Храню последние 20 сообщений \\(10 пар запрос-ответ\\)",
        parse_mode=ParseMode.MARKDOWN
    )


@dp.message(Command("new_chat"))
async def cmd_new_chat(message: Message):
    thread_id = message.message_thread_id or 0
    session_key = (message.from_user.id, thread_id)
    sessions.pop(session_key, None)
    await message.answer("Контекст сброшен. Начинаем заново!")


@dp.message(Command("new_topic"))
async def cmd_new_topic(message: Message):
    """Создает новую тему в приватном чате (Bot API 9.3)"""
    try:
        # Получаем название темы из аргументов команды
        args = message.text.split(maxsplit=1)
        topic_name = args[1] if len(args) > 1 else "Новая корзина"
        
        # Создаем форум-топик в приватном чате (Bot API 9.3)
        result = await bot.create_forum_topic(
            chat_id=message.chat.id,
            name=topic_name,
            icon_color=0x6FB9F0,  # Голубой цвет
            icon_custom_emoji_id=None
        )
        
        # Отправляем приветствие в новый топик
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


@dp.callback_query(F.data == "new_basket")
async def callback_new_basket(callback: CallbackQuery):
    thread_id = callback.message.message_thread_id or 0
    session_key = (callback.from_user.id, thread_id)
    sessions.pop(session_key, None)
    await callback.answer()
    await callback.message.answer("Начинаем собирать новую корзину! Что приготовим?")


async def notify_admins(message: Message, response: str = None):
    """Пересылает запрос пользователя администраторам"""
    user_info = f"👤 {message.from_user.full_name}"
    if message.from_user.username:
        user_info += f" (@{message.from_user.username})"
    user_info += f" [ID: {message.from_user.id}]"
    
    notification = f"📨 Новый запрос:\n{user_info}\n\n💬 Сообщение: {message.text}"
    
    if response:
        notification += f"\n\n🤖 Ответ бота:\n{response[:500]}"
        if len(response) > 500:
            notification += "..."
    
    for admin_id in ADMIN_IDS:
        if admin_id != message.from_user.id:  # Не отправляем админу его же сообщения
            try:
                await bot.send_message(admin_id, notification)
            except Exception as e:
                log.error(f"❌ Не удалось отправить уведомление админу {admin_id}: {e}")


@dp.message(F.text)
async def handle_message(message: Message):
    user_id = message.from_user.id
    lock = get_user_lock(user_id)

    if lock.locked():
        await message.answer("⏳ Подожди, обрабатываю предыдущий запрос...")
        return

    async with lock:
        progress_msg = None
        stream_msg = None
        is_streaming = False

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
            """Стриминг текста через sendMessageDraft (Bot API 9.3)"""
            nonlocal stream_msg, is_streaming, progress_msg
            
            # Удаляем прогресс-сообщение при первом стриме
            if not is_streaming and progress_msg:
                try:
                    await progress_msg.delete()
                    progress_msg = None
                except:
                    pass
            
            # Убираем thinking теги из стрима
            display_text = text
            if "<think>" in display_text:
                think_end = display_text.find("</think>")
                if think_end > 0:
                    display_text = display_text[think_end+8:].strip()
            
            if not display_text:
                return
            
            try:
                thread_id = message.message_thread_id
                
                # Используем sendMessageDraft для стриминга (Bot API 9.3)
                # Пока aiogram не поддерживает это, используем прямой API вызов
                result = await bot.session.post(
                    f"{bot.session.api.base}/bot{bot.token}/sendMessageDraft",
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
                        # Сохраняем ID сообщения для последующих обновлений
                        from aiogram.types import Message as TgMessage
                        stream_msg = TgMessage(**data["result"])
                        is_streaming = True
                        
            except Exception as e:
                # Fallback на обычный editMessageText с ограничением частоты
                log.debug(f"sendMessageDraft не поддерживается, используем editMessageText: {e}")
                try:
                    if not stream_msg:
                        stream_msg = await message.answer(display_text + " ▌", parse_mode=ParseMode.MARKDOWN)
                        is_streaming = True
                    else:
                        # Обновляем не чаще раза в секунду
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
            response = await run_agent(user_id, username, message.text, send_progress, stream_text, thread_id)

            # Удаляем прогресс-сообщение если оно еще есть
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

            # Финальное сообщение
            if stream_msg:
                # Обновляем стрим-сообщение финальным ответом (убираем курсор)
                try:
                    await stream_msg.edit_text(response, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
                except:
                    await stream_msg.edit_text(response, reply_markup=keyboard)
            else:
                # Если стриминг не использовался, отправляем обычное сообщение
                try:
                    await message.answer(response, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
                except:
                    await message.answer(response, reply_markup=keyboard)
            
            # Уведомляем администраторов о запросе
            await notify_admins(message, response)

        except Exception as e:
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


async def main():
    log.info("🚀 Бот запущен")
    
    # Уведомляем администраторов о старте
    startup_message = "🚀 *Бот VkusVill AI запущен!*\n\n✅ Система готова к работе\n🤖 Модель: Claude Haiku 4.5\n⚡ Стриминг ответов активирован"
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, startup_message, parse_mode=ParseMode.MARKDOWN)
            log.info(f"✅ Уведомление о старте отправлено админу {admin_id}")
        except Exception as e:
            log.error(f"❌ Не удалось отправить уведомление о старте админу {admin_id}: {e}")
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
