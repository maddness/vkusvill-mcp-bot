# Инструкция по настройке автоматического деплоя

## Что уже сделано

✅ Создан GitHub Actions workflow файл `.github/workflows/deploy.yml`
✅ Настроена автоматическая раскатка при push/merge в `main`

## Что нужно сделать для запуска

### 1. Создать SSH ключ для деплоя (если еще нет)

На вашем сервере:

```bash
# Создать новый SSH ключ специально для деплоя
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_deploy

# Добавить публичный ключ в authorized_keys
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys

# Вывести приватный ключ (его нужно скопировать для GitHub)
cat ~/.ssh/github_deploy
```

### 2. Настроить GitHub Secrets

Перейдите в ваш GitHub репозиторий:
`https://github.com/maddness/vkusvill-mcp-bot/settings/secrets/actions`

Добавьте следующие секреты (Settings → Secrets and variables → Actions → New repository secret):

| Имя секрета | Значение | Описание |
|-------------|----------|----------|
| `SERVER_HOST` | IP адрес или домен сервера | Например: `123.45.67.89` |
| `SERVER_USER` | `ubuntu` | Пользователь для SSH подключения |
| `SERVER_PORT` | `22` | SSH порт (обычно 22) |
| `SSH_PRIVATE_KEY` | Содержимое файла `~/.ssh/github_deploy` | Приватный SSH ключ (полностью, включая `-----BEGIN` и `-----END`) |

### 3. Проверить настройки на сервере

На сервере должно быть:

```bash
# Проверить, что репозиторий уже склонирован
ls -la /home/ubuntu/vkusvill-mcp-bot

# Проверить, что podman-compose установлен
which podman-compose

# Проверить, что конфиг существует
ls -la /home/ubuntu/vkusvill-mcp-bot/config.yaml
```

### 4. Создать feature-ветку и протестировать деплой

```bash
# Сбросить локальные изменения (если они не нужны)
git restore .

# Создать feature-ветку для теста деплоя
git checkout -b feature/setup-ci-cd

# Добавить изменения
git add .github/workflows/deploy.yml
git add DEPLOYMENT.md

# Закоммитить
git commit -m "feat: настроить CI/CD для автоматического деплоя

- Добавлен GitHub Actions workflow
- Автоматический деплой при push в main
- Инструкция по настройке"

# Запушить в feature-ветку
git push -u origin feature/setup-ci-cd

# Создать PR
gh pr create --title "Настроить CI/CD для автоматического деплоя" \
  --body "Добавлен GitHub Actions workflow для автоматической раскатки при merge в main"
```

### 5. После настройки секретов - протестировать

1. **Слить PR в main** (после вашего аппрува)
2. **GitHub Actions автоматически запустит деплой**
3. **Проверить логи деплоя**: `https://github.com/maddness/vkusvill-mcp-bot/actions`

### 6. Проверить работу бота на сервере

```bash
# Подключиться к серверу
ssh ubuntu@ваш-сервер

# Проверить логи
cd /home/ubuntu/vkusvill-mcp-bot
podman-compose logs -f
```

## Как это работает

После настройки:

1. **Вы делаете изменения** в feature-ветке
2. **Создаете PR** в main
3. **Аппрувите и мержите PR**
4. **GitHub Actions автоматически**:
   - Подключается к серверу по SSH
   - Делает `git pull origin main`
   - Перезапускает контейнеры с новой версией
5. **Бот обновлен** и работает с новым кодом

## Ручной деплой (если нужно)

Можно запустить деплой вручную через GitHub UI:
`https://github.com/maddness/vkusvill-mcp-bot/actions/workflows/deploy.yml` → Run workflow

## Безопасность

- ✅ Используется отдельный SSH ключ только для деплоя
- ✅ Приватный ключ хранится в GitHub Secrets (зашифрован)
- ✅ Доступ только к одному серверу
- ✅ Только из main ветки

## Альтернатива: Docker Hub

Если хотите более продвинутый деплой через Docker Hub:

1. Собирать образ на GitHub Actions
2. Пушить в Docker Hub
3. На сервере пуллить новый образ и перезапускать

Дайте знать, если нужна эта схема!

