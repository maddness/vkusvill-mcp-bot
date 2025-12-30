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

bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
dp = Dispatcher()
sessions: dict[int, list] = {}
user_locks: dict[int, asyncio.Lock] = {}

MAX_HISTORY_MESSAGES = 10
MCP_URL = "https://mcp001.vkusvill.ru/mcp"

SYSTEM_PROMPT = """Ты помощник для сбора продуктовых корзин ВкусВилл.

Правила:
- Если уверен в рецепте - сразу ищи, не спрашивай
- Если не уверен - спроси коротко
- Бери ПЕРВЫЙ подходящий товар из поиска
- В конце создай ссылку через create_cart

Формат ответа:

🛒 *КОРЗИНА ДЛЯ [НАЗВАНИЕ]*

1. 🥔 Картофель — 1 кг — *47 ₽*
2. 🥕 Морковь — 1 кг — *48 ₽*

💰 *Итого: XXX ₽*

[🛍 Перейти в корзину](ссылка)

✨ Приятных покупок!

Формат: без таблиц, *жирный* для цен, [текст](url) для ссылок."""

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
    """Поиск товаров ВкусВилл по названию. Возвращает список товаров с id, названием и ценой."""
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

        # Оставляем только: xml_id, name, price (минимум для корзины)
        filtered = []
        for p in products[:5]:  # Берём только 5 товаров
            filtered.append({
                "xml_id": p.get("xml_id"),
                "name": p.get("name", "")[:50],  # Обрезаем название
                "price": p.get("price")
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

    agent = Agent(
        name="VkusVill Assistant",
        model="litellm/anthropic/claude-haiku-4-5-20251001",
        instructions=SYSTEM_PROMPT,
        tools=[search_products, create_cart],
        model_settings=ModelSettings(include_usage=True),
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

    # Логируем использование токенов
    try:
        usage = result.context_wrapper.usage
        log.info(f"📊 Токены: input={usage.input_tokens}, output={usage.output_tokens}, total={usage.total_tokens}")
    except:
        pass

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
                    [InlineKeyboardButton(text="Собрать новую корзину", callback_data="new_basket")]
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
