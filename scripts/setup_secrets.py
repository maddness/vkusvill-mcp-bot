#!/usr/bin/env python3
"""
Скрипт для настройки GitHub Secrets через API.

Использует GitHub API для добавления секретов в репозиторий,
чтобы настроить автоматический деплой через GitHub Actions.
"""

import base64
import json
import socket
import subprocess
import sys
from getpass import getpass

import requests
from nacl import encoding, public


def encrypt_secret(public_key: str, secret_value: str) -> str:
    """
    Шифруем секрет используя публичный ключ репозитория.
    
    Args:
        public_key: Публичный ключ репозитория в base64
        secret_value: Значение секрета для шифрования
        
    Returns:
        str: Зашифрованное значение в base64
    """
    public_key_obj = public.PublicKey(
        public_key.encode("utf-8"), 
        encoding.Base64Encoder()
    )
    sealed_box = public.SealedBox(public_key_obj)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def get_public_key(owner: str, repo: str, token: str) -> tuple[str, str]:
    """
    Получаем публичный ключ репозитория для шифрования секретов.
    
    Args:
        owner: Владелец репозитория (username или организация)
        repo: Название репозитория
        token: GitHub Personal Access Token
        
    Returns:
        tuple: (key_id, public_key)
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/secrets/public-key"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    
    data = response.json()
    return data["key_id"], data["key"]


def set_secret(
    owner: str, 
    repo: str, 
    token: str, 
    secret_name: str, 
    secret_value: str,
    key_id: str,
    public_key: str
) -> None:
    """
    Устанавливаем секрет в репозиторий через GitHub API.
    
    Args:
        owner: Владелец репозитория
        repo: Название репозитория
        token: GitHub Personal Access Token
        secret_name: Имя секрета
        secret_value: Значение секрета
        key_id: ID публичного ключа
        public_key: Публичный ключ для шифрования
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/secrets/{secret_name}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    
    encrypted_value = encrypt_secret(public_key, secret_value)
    
    data = {
        "encrypted_value": encrypted_value,
        "key_id": key_id
    }
    
    response = requests.put(url, headers=headers, json=data)
    response.raise_for_status()
    
    print(f"✅ Секрет {secret_name} успешно установлен")


def get_server_ip() -> str:
    """
    Определяем IP адрес текущего сервера.
    
    Returns:
        str: IP адрес сервера
    """
    try:
        # Пробуем получить IP через hostname -I
        result = subprocess.run(
            ["hostname", "-I"], 
            capture_output=True, 
            text=True, 
            check=True
        )
        ip = result.stdout.strip().split()[0]
        return ip
    except Exception:
        # Fallback: получаем IP через socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return ""


def main() -> None:
    """
    Основная функция для интерактивной настройки секретов.
    Запрашивает у пользователя все необходимые данные и создает секреты.
    """
    print("=" * 60)
    print("🔐 Настройка GitHub Secrets для автоматического деплоя")
    print("=" * 60)
    print()
    
    # Запрашиваем данные репозитория
    owner = input("Владелец репозитория (username/org) [maddness]: ").strip() or "maddness"
    repo = input("Название репозитория [vkusvill-mcp-bot]: ").strip() or "vkusvill-mcp-bot"
    
    print()
    print("GitHub Personal Access Token:")
    print("(получите его на https://github.com/settings/tokens)")
    token = getpass("Введите токен (ввод скрыт): ").strip()
    
    if not token:
        print("❌ Токен не может быть пустым!")
        sys.exit(1)
    
    print()
    print("Получаем публичный ключ репозитория...")
    
    try:
        key_id, public_key = get_public_key(owner, repo, token)
        print(f"✅ Публичный ключ получен (ID: {key_id[:8]}...)")
    except requests.exceptions.HTTPError as e:
        print(f"❌ Ошибка при получении ключа: {e}")
        print("Проверьте токен и права доступа!")
        sys.exit(1)
    
    print()
    print("=" * 60)
    print("Теперь введите значения секретов:")
    print("=" * 60)
    print()
    
    # Запрашиваем значения секретов
    secrets = {}
    
    # Определяем IP сервера автоматически
    server_ip = get_server_ip()
    default_host = f" [{server_ip}]" if server_ip else ""
    
    print(f"1. SERVER_HOST - IP адрес или домен сервера{default_host}")
    host_input = input("   Введите значение: ").strip()
    secrets["SERVER_HOST"] = host_input if host_input else server_ip
    
    print()
    print("2. SERVER_USER - пользователь SSH (обычно ubuntu)")
    secrets["SERVER_USER"] = input("   Введите значение [ubuntu]: ").strip() or "ubuntu"
    
    print()
    print("3. SERVER_PORT - порт SSH (обычно 22)")
    secrets["SERVER_PORT"] = input("   Введите значение [22]: ").strip() or "22"
    
    print()
    print("4. SSH_PRIVATE_KEY - приватный SSH ключ")
    print("   Вставьте полностью ключ (включая -----BEGIN и -----END)")
    print("   Нажмите Enter, затем Ctrl+D для завершения ввода:")
    
    lines = []
    try:
        while True:
            line = input()
            lines.append(line)
    except EOFError:
        pass
    
    secrets["SSH_PRIVATE_KEY"] = "\n".join(lines)
    
    # Проверяем, что все секреты заполнены
    if not all(secrets.values()):
        print()
        print("❌ Не все секреты заполнены!")
        sys.exit(1)
    
    # Устанавливаем секреты
    print()
    print("=" * 60)
    print("Устанавливаем секреты...")
    print("=" * 60)
    print()
    
    for secret_name, secret_value in secrets.items():
        try:
            set_secret(owner, repo, token, secret_name, secret_value, key_id, public_key)
        except requests.exceptions.HTTPError as e:
            print(f"❌ Ошибка при установке {secret_name}: {e}")
            sys.exit(1)
    
    print()
    print("=" * 60)
    print("✅ Все секреты успешно установлены!")
    print("=" * 60)
    print()
    print("Теперь можно запушить изменения и GitHub Actions")
    print("автоматически задеплоит бота на сервер при merge в main!")
    print()
    print("Проверить секреты можно здесь:")
    print(f"https://github.com/{owner}/{repo}/settings/secrets/actions")


if __name__ == "__main__":
    main()

