# ⚡ Быстрый старт (3 команды)

## 1️⃣ Создать SSH ключ

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_deploy
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
cat ~/.ssh/github_deploy  # Скопировать вывод!
```

## 2️⃣ Установить секреты

```bash
pip3 install requests PyNaCl
python3 scripts/setup_secrets.py
```

Ввести:
- Token: ваш GitHub Personal Access Token
- HOST: IP этого сервера
- USER: `ubuntu`
- PORT: `22`
- KEY: вставить из шага 1, Ctrl+D

## 3️⃣ Создать PR и смержить

```bash
git restore .
git checkout -b feature/setup-ci-cd
git add .github/ DEPLOYMENT.md DEPLOY_SETUP.md QUICKSTART.md scripts/
git commit -m "feat: настроить CI/CD"
git push -u origin feature/setup-ci-cd
gh pr create --title "CI/CD setup" --body "Auto-deploy setup"
```

✅ После мерджа в main → автодеплой!

Логи: https://github.com/maddness/vkusvill-mcp-bot/actions

