#!/bin/bash
# Скрипт для быстрой настройки CI/CD

set -e

echo "========================================================"
echo "🚀 Быстрая настройка автоматического деплоя"
echo "========================================================"
echo ""

# Шаг 1: Проверка зависимостей
echo "📦 Шаг 1/4: Проверка зависимостей..."
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 не установлен!"
    exit 1
fi

# Установка Python зависимостей
pip3 install -q requests PyNaCl 2>/dev/null || {
    echo "⚠️  Устанавливаем зависимости..."
    pip3 install requests PyNaCl
}
echo "✅ Зависимости установлены"
echo ""

# Шаг 2: Создание SSH ключа
echo "🔑 Шаг 2/4: Создание SSH ключа для деплоя..."
if [ -f ~/.ssh/github_deploy ]; then
    echo "⚠️  SSH ключ уже существует (~/.ssh/github_deploy)"
    read -p "   Пересоздать? (y/N): " recreate
    if [[ $recreate =~ ^[Yy]$ ]]; then
        rm -f ~/.ssh/github_deploy ~/.ssh/github_deploy.pub
    fi
fi

if [ ! -f ~/.ssh/github_deploy ]; then
    ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_deploy -N ""
    cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
    echo "✅ SSH ключ создан"
else
    echo "✅ Используем существующий SSH ключ"
fi
echo ""

# Шаг 3: Настройка секретов через API
echo "🔐 Шаг 3/4: Настройка GitHub Secrets..."
echo ""
python3 scripts/setup_secrets.py
echo ""

# Шаг 4: Создание PR
echo "📝 Шаг 4/4: Создание Pull Request..."
echo ""

# Сбрасываем ненужные изменения
git restore . 2>/dev/null || true

# Проверяем, не на ветке ли мы уже
current_branch=$(git branch --show-current)
if [ "$current_branch" = "feature/setup-ci-cd" ]; then
    echo "⚠️  Уже на ветке feature/setup-ci-cd"
else
    # Создаем новую ветку
    git checkout -b feature/setup-ci-cd 2>/dev/null || git checkout feature/setup-ci-cd
fi

# Добавляем файлы
git add .github/ *.md scripts/ 2>/dev/null || true

# Проверяем, есть ли изменения
if git diff --cached --quiet; then
    echo "⚠️  Нет изменений для коммита"
else
    # Коммитим
    git commit -m "feat: настроить CI/CD для автоматического деплоя

- Добавлен GitHub Actions workflow для автодеплоя
- Добавлен скрипт для настройки секретов через API
- Добавлена документация по настройке
- Настроена автоматическая раскатка при merge в main"
    
    echo "✅ Изменения закоммичены"
fi

# Пушим
echo ""
read -p "Запушить изменения в GitHub? (Y/n): " push_confirm
if [[ ! $push_confirm =~ ^[Nn]$ ]]; then
    git push -u origin feature/setup-ci-cd
    echo "✅ Изменения запушены"
    
    # Создаем PR
    echo ""
    read -p "Создать Pull Request? (Y/n): " pr_confirm
    if [[ ! $pr_confirm =~ ^[Nn]$ ]]; then
        if command -v gh &> /dev/null; then
            gh pr create \
                --title "Настроить CI/CD для автоматического деплоя" \
                --body "🚀 Настроен полный CI/CD pipeline для автоматической раскатки на сервер при merge в main

## Что добавлено:
- ✅ GitHub Actions workflow для автоматического деплоя
- ✅ Скрипт для настройки секретов через API
- ✅ Подробная документация
- ✅ GitHub Secrets настроены

## Как это работает:
1. После merge в main запускается GitHub Actions
2. Подключается к серверу по SSH
3. Делает git pull
4. Пересобирает и перезапускает контейнеры

## После мерджа:
- Проверить логи деплоя: https://github.com/maddness/vkusvill-mcp-bot/actions
- Проверить работу бота на сервере: \`podman-compose logs -f\`" 2>/dev/null || echo "⚠️  Не удалось создать PR через gh cli"
            
            echo "✅ Pull Request создан!"
        else
            echo "⚠️  GitHub CLI (gh) не установлен"
            echo "   Создайте PR вручную: https://github.com/maddness/vkusvill-mcp-bot/compare/feature/setup-ci-cd"
        fi
    fi
fi

echo ""
echo "========================================================"
echo "✅ Настройка завершена!"
echo "========================================================"
echo ""
echo "📋 Следующие шаги:"
echo "  1. Проверьте PR на GitHub"
echo "  2. Смержите PR в main"
echo "  3. GitHub Actions автоматически задеплоит бота"
echo "  4. Проверьте логи: https://github.com/maddness/vkusvill-mcp-bot/actions"
echo ""
echo "🔍 Проверить секреты:"
echo "  https://github.com/maddness/vkusvill-mcp-bot/settings/secrets/actions"
echo ""
