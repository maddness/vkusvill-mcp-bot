# VkusVill Telegram Bot

Telegram-бот для сбора продуктовых корзин ВкусВилл с использованием AI.

<p align="center">
  <img src="assets/pic.jpg" alt="Demo" width="400">
</p>

## Что делает бот

- Принимает запросы на сбор продуктов (например, "Собери корзину для салата оливье")
- Использует Claude AI для понимания рецептов и подбора ингредиентов
- Ищет товары через MCP (Model Context Protocol) ВкусВилл
- Формирует корзину и генерирует ссылку для оформления заказа

## Стек

- **[OpenAI Agents SDK](https://github.com/openai/openai-agents-python)** — агентский фреймворк с поддержкой MCP
- **[LiteLLM](https://github.com/BerriAI/litellm)** — прокси для работы с Claude через OpenAI-совместимый интерфейс
- **[aiogram](https://github.com/aiogram/aiogram)** — асинхронный Telegram Bot API
- **Claude Haiku 4.5** — языковая модель

## Установка и запуск

### 1. Клонировать репозиторий

```bash
git clone https://github.com/maddness/vkusvill-mcp-bot.git
cd vkusvill-mcp-bot
```

### 2. Создать виртуальное окружение

```bash
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# или
venv\Scripts\activate  # Windows
```

### 3. Установить зависимости

```bash
pip install -r requirements.txt
```

### 4. Настроить переменные окружения

```bash
cp .env.example .env
```

Заполнить `.env`:

| Переменная | Описание |
|------------|----------|
| `TELEGRAM_BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) |
| `ANTHROPIC_API_KEY` | API ключ от [Anthropic](https://console.anthropic.com/) |

### 5. Запустить бота

```bash
python main.py
```

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Начать работу с ботом |
| `/new_chat` | Сбросить контекст и начать заново |

## Использование

1. Отправьте боту сообщение с описанием того, что хотите приготовить
2. Бот найдёт нужные продукты во ВкусВилл
3. Получите готовую корзину со ссылкой на оформление заказа
4. Нажмите кнопку "Собрать новую корзину" для нового запроса

## Лицензия

MIT
