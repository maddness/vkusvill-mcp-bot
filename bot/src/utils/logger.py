"""
Модуль для логирования запросов и ответов агента
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


class AgentLogger:
    """Логгер для сохранения запросов и ответов агента"""
    
    def __init__(self, logs_dir: str = "logs"):
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(exist_ok=True)
    
    def log_interaction(
        self,
        user_id: int,
        username: str,
        query: str,
        response: str,
        tools_used: list = None,
        tokens: Dict[str, int] = None,
        error: str = None
    ):
        """
        Сохраняет взаимодействие пользователя с ботом
        
        Args:
            user_id: ID пользователя Telegram
            username: Username пользователя
            query: Запрос пользователя
            response: Ответ агента
            tools_used: Список использованных инструментов
            tokens: Информация о токенах (input, output, total)
            error: Текст ошибки, если была
        """
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
        
        # Создаем папку для даты
        date_dir = self.logs_dir / date_str
        date_dir.mkdir(exist_ok=True)
        
        # Создаем папку для пользователя
        user_dir = date_dir / str(user_id)
        user_dir.mkdir(exist_ok=True)
        
        # Формируем данные для логирования
        log_data = {
            "timestamp": now.isoformat(),
            "user_id": user_id,
            "username": username,
            "query": query,
            "response": response,
            "tools_used": tools_used or [],
            "tokens": tokens or {},
            "error": error
        }
        
        # Сохраняем в файл
        log_file = user_dir / f"{timestamp}.json"
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        
        return log_file
    
    def get_user_logs(self, user_id: int, date: str = None) -> list:
        """
        Получает логи пользователя за определенную дату
        
        Args:
            user_id: ID пользователя
            date: Дата в формате YYYY-MM-DD (если None - сегодня)
        
        Returns:
            Список логов
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        user_dir = self.logs_dir / date / str(user_id)
        if not user_dir.exists():
            return []
        
        logs = []
        for log_file in sorted(user_dir.glob("*.json")):
            with open(log_file, 'r', encoding='utf-8') as f:
                logs.append(json.load(f))
        
        return logs
    
    def get_all_dates(self) -> list:
        """Возвращает список всех дат с логами"""
        return sorted([d.name for d in self.logs_dir.iterdir() if d.is_dir()])
    
    def get_users_for_date(self, date: str) -> list:
        """Возвращает список user_id для определенной даты"""
        date_dir = self.logs_dir / date
        if not date_dir.exists():
            return []
        
        return sorted([int(d.name) for d in date_dir.iterdir() if d.is_dir()])

