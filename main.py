import asyncio
import os
import logging
import httpx
from dotenv import load_dotenv

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode

from agents import Agent, Runner, set_default_openai_api, set_tracing_disabled, function_tool, ModelSettings

load_dotenv()

os.environ["SSL_VERIFY"] = "false"

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
sessions: dict[int, list] = {}
user_locks: dict[int, asyncio.Lock] = {}

MAX_HISTORY_MESSAGES = 10
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


async def run_agent(user_id: int, user_message: str, send_progress) -> str:
    log.info(f"👤 User {user_id}: {user_message[:50]}...")

    if user_id not in sessions:
        sessions[user_id] = []

    sessions[user_id].append({"role": "user", "content": user_message})

    if len(sessions[user_id]) > MAX_HISTORY_MESSAGES:
        sessions[user_id] = sessions[user_id][-MAX_HISTORY_MESSAGES:]

    settings = ModelSettings(include_usage=True)

    agent = Agent(
        name="VkusVill Assistant",
        model=MODEL_NAME,
        instructions=SYSTEM_PROMPT,
        tools=[search_products, create_cart],
        model_settings=settings,
    )

    result = Runner.run_streamed(agent, sessions[user_id])

    async for event in result.stream_events():
        if event.type == "run_item_stream_event":
            item = event.item
            if hasattr(item, 'raw_item') and hasattr(item.raw_item, 'name'):
                tool_name = item.raw_item.name
                if "search" in tool_name:
                    await send_progress("Ищу товары...")
                elif "cart" in tool_name:
                    await send_progress("Собираю корзину...")

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

    sessions[user_id].append({"role": "assistant", "content": final})
    log.info(f"✅ Ответ готов ({len(final)} символов)")
    return final


@dp.message(Command("start"))
async def cmd_start(message: Message):
    sessions.pop(message.from_user.id, None)
    await message.answer(
        "Привет! Я помогу собрать корзину продуктов ВкусВилл.\n\n"
        "Напиши что хочешь приготовить или какие продукты нужны."
    )


@dp.message(Command("new_chat"))
async def cmd_new_chat(message: Message):
    sessions.pop(message.from_user.id, None)
    await message.answer("Контекст сброшен. Начинаем заново!")


@dp.callback_query(F.data == "new_basket")
async def callback_new_basket(callback: CallbackQuery):
    sessions.pop(callback.from_user.id, None)
    await callback.answer()
    await callback.message.answer("Начинаем собирать новую корзину! Что приготовим?")


@dp.message(F.text)
async def handle_message(message: Message):
    user_id = message.from_user.id
    lock = get_user_lock(user_id)

    if lock.locked():
        await message.answer("⏳ Подожди, обрабатываю предыдущий запрос...")
        return

    async with lock:
        progress_msg = None

        async def send_progress(text: str):
            nonlocal progress_msg
            if progress_msg:
                try:
                    await progress_msg.edit_text(text)
                except:
                    pass
            else:
                progress_msg = await message.answer(text)

        await send_progress("Думаю...")

        try:
            response = await run_agent(user_id, message.text, send_progress)

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

            try:
                await message.answer(response, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
            except:
                await message.answer(response, reply_markup=keyboard)

        except Exception as e:
            if progress_msg:
                try:
                    await progress_msg.delete()
                except:
                    pass
            await message.answer(f"Произошла ошибка: {e}")


async def main():
    log.info("🚀 Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
