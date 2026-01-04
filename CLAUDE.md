# Claude Code Instructions

## Запуск бота локально

### Podman (предпочтительно)

```bash
# Запуск
podman-compose up -d --build

# Логи
podman-compose logs -f

# Остановка
podman-compose down

# Перезапуск с пересборкой
podman-compose down && podman-compose up -d --build
```

### Docker

```bash
# Запуск
docker-compose up -d --build

# Логи
docker-compose logs -f

# Остановка
docker-compose down

# Перезапуск с пересборкой
docker-compose down && docker-compose up -d --build
```

## Правила работы с репозиторием

1. **НИКОГДА не пушить напрямую в main** — только через feature-ветки и PR
2. **Создавать ветку для каждого изменения:**
   ```bash
   git checkout main
   git pull
   git checkout -b fix/описание-фикса
   ```
3. **После коммита — пуш в ветку и создание PR:**
   ```bash
   git push -u origin fix/описание-фикса
   gh pr create --title "Описание" --body "Детали"
   ```
4. **Ждать аппрува от пользователя** перед мержем PR
5. **Не мержить PR самостоятельно** без явного разрешения
