"""
Модуль для работы с базой данных пользователей
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class UserDatabase:
    """База данных пользователей"""
    
    def __init__(self, db_dir: str = "db"):
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(exist_ok=True)
        self.users_file = self.db_dir / "users.json"
        self.interactions_file = self.db_dir / "interactions.json"
        
        # Инициализируем файлы, если их нет
        if not self.users_file.exists():
            self._save_users({})
        
        if not self.interactions_file.exists():
            self._save_interactions({})
    
    def _load_users(self) -> Dict:
        """Загружает данные пользователей"""
        with open(self.users_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _save_users(self, users: Dict):
        """Сохраняет данные пользователей"""
        with open(self.users_file, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    
    def _load_interactions(self) -> Dict:
        """Загружает статистику взаимодействий"""
        with open(self.interactions_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _save_interactions(self, interactions: Dict):
        """Сохраняет статистику взаимодействий"""
        with open(self.interactions_file, 'w', encoding='utf-8') as f:
            json.dump(interactions, f, ensure_ascii=False, indent=2)
    
    def add_user(
        self,
        user_id: int,
        username: str = None,
        first_name: str = None,
        last_name: str = None
    ) -> Dict:
        """
        Добавляет или обновляет пользователя
        
        Args:
            user_id: ID пользователя Telegram
            username: Username пользователя
            first_name: Имя
            last_name: Фамилия
        
        Returns:
            Данные пользователя
        """
        users = self._load_users()
        user_id_str = str(user_id)
        
        now = datetime.now().isoformat()
        
        if user_id_str in users:
            # Обновляем существующего пользователя
            user = users[user_id_str]
            user["last_interaction"] = now
            if username:
                user["username"] = username
            if first_name:
                user["first_name"] = first_name
            if last_name:
                user["last_name"] = last_name
        else:
            # Создаем нового пользователя
            user = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "first_interaction": now,
                "last_interaction": now,
                "total_interactions": 0
            }
            users[user_id_str] = user
        
        self._save_users(users)
        return user
    
    def log_interaction(self, user_id: int):
        """
        Логирует взаимодействие пользователя
        
        Args:
            user_id: ID пользователя
        """
        interactions = self._load_interactions()
        user_id_str = str(user_id)
        date_str = datetime.now().strftime("%Y-%m-%d")
        
        # Обновляем общую статистику
        if user_id_str not in interactions:
            interactions[user_id_str] = {
                "total": 0,
                "by_date": {}
            }
        
        interactions[user_id_str]["total"] += 1
        
        # Обновляем статистику по датам
        if date_str not in interactions[user_id_str]["by_date"]:
            interactions[user_id_str]["by_date"][date_str] = 0
        
        interactions[user_id_str]["by_date"][date_str] += 1
        
        # Обновляем счетчик в users
        users = self._load_users()
        if user_id_str in users:
            users[user_id_str]["total_interactions"] = interactions[user_id_str]["total"]
            self._save_users(users)
        
        self._save_interactions(interactions)
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """
        Получает данные пользователя
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Данные пользователя или None
        """
        users = self._load_users()
        return users.get(str(user_id))
    
    def get_all_users(self) -> List[Dict]:
        """Возвращает список всех пользователей"""
        users = self._load_users()
        return list(users.values())
    
    def get_user_stats(self, user_id: int) -> Optional[Dict]:
        """
        Получает статистику пользователя
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Статистика или None
        """
        interactions = self._load_interactions()
        return interactions.get(str(user_id))
    
    def get_total_users(self) -> int:
        """Возвращает общее количество пользователей"""
        users = self._load_users()
        return len(users)
    
    def get_active_users_today(self) -> int:
        """Возвращает количество активных пользователей сегодня"""
        interactions = self._load_interactions()
        date_str = datetime.now().strftime("%Y-%m-%d")
        
        count = 0
        for user_data in interactions.values():
            if date_str in user_data.get("by_date", {}):
                count += 1
        
        return count
    
    def get_stats(self) -> Dict:
        """Возвращает общую статистику"""
        users = self._load_users()
        interactions = self._load_interactions()
        
        total_interactions = sum(
            data["total"] for data in interactions.values()
        )
        
        return {
            "total_users": len(users),
            "active_today": self.get_active_users_today(),
            "total_interactions": total_interactions
        }

