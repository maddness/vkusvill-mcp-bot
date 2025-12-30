import asyncio
import os
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode

from agents import Agent, Runner, set_default_openai_api, set_tracing_disabled
from agents.mcp import MCPServerStreamableHttp

load_dotenv()

os.environ["SSL_VERIFY"] = "false"

bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
dp = Dispatcher()
sessions: dict[int, list] = {}

SYSTEM_PROMPT = """Ты помощник для сбора продуктовых корзин ВкусВилл.

Правила:
- Если ты уверен в рецепте и ингредиентах - сразу ищи товары, не задавай вопросов
- Если не уверен - задай уточняющие вопросы пользователю
- Используй vkusvill_products_search для поиска товаров
- Используй vkusvill_product_details если нужны детали (состав, КБЖУ)
- В конце ОБЯЗАТЕЛЬНО создай ссылку на корзину через vkusvill_cart_link_create

Формат финального ответа (используй эмодзи!):

🛒 *КОРЗИНА ДЛЯ [НАЗВАНИЕ]*

1. 🥔 Картофель — 1 кг — *47 ₽*
2. 🥕 Морковь — 1 кг — *48 ₽*
...

💰 *Итого: XXX ₽*

[🛍 Перейти в корзину](ссылка)

✨ Приятных покупок!

Важно:
- НЕ используй таблицы и ## заголовки
- Используй *жирный* для выделения
- Каждый товар на новой строке с номером и эмодзи
- Ссылка на корзину как [текст](url)"""

MCP_URL = "https://mcp001.vkusvill.ru/mcp"

set_default_openai_api("chat_completions")
set_tracing_disabled(True)


async def run_agent(user_id: int, user_message: str, send_progress) -> str:
    if user_id not in sessions:
        sessions[user_id] = []

    sessions[user_id].append({"role": "user", "content": user_message})

    async with MCPServerStreamableHttp(
        name="vkusvill",
        params={"url": MCP_URL},
        cache_tools_list=True,
        client_session_timeout_seconds=60,
    ) as server:
        agent = Agent(
            name="VkusVill Assistant",
            model="litellm/anthropic/claude-haiku-4-5-20251001",
            instructions=SYSTEM_PROMPT,
            mcp_servers=[server],
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
        sessions[user_id].append({"role": "assistant", "content": final})
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
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
