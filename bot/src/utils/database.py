"""
Модуль для работы с базой данных пользователей и сессий (PostgreSQL)
"""
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Any
from sqlalchemy import create_engine, and_
from sqlalchemy.orm import sessionmaker, Session as DBSession
from sqlalchemy.exc import SQLAlchemyError

from bot.src.utils.models import Base, User, Session, Interaction

log = logging.getLogger(__name__)

# Получаем DATABASE_URL из переменных окружения
DATABASE_URL = os.getenv("DATABASE_URL")

# Создаем engine и session maker
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine)

# Создаем таблицы при инициализации
Base.metadata.create_all(engine)


class UserDatabase:
    """База данных пользователей (PostgreSQL)"""
    
    def __init__(self):
        """Инициализация базы данных"""
        log.info(f"📊 UserDatabase: подключение к PostgreSQL")
    
    def _get_db(self) -> DBSession:
        """Создает новую сессию БД"""
        return SessionLocal()
    
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
        db = self._get_db()
        try:
            # Ищем существующего пользователя
            user = db.query(User).filter(User.user_id == user_id).first()
            
            now = datetime.utcnow()
            
            if user:
                # Обновляем существующего
                user.last_interaction = now
                if username:
                    user.username = username
                if first_name:
                    user.first_name = first_name
                if last_name:
                    user.last_name = last_name
            else:
                # Создаем нового
                user = User(
                    user_id=user_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    first_interaction=now,
                    last_interaction=now,
                    total_interactions=0,
                    is_banned=False
                )
                db.add(user)
            
            db.commit()
            db.refresh(user)
            
            return {
                "user_id": user.user_id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "first_interaction": user.first_interaction.isoformat() if user.first_interaction else None,
                "last_interaction": user.last_interaction.isoformat() if user.last_interaction else None,
                "total_interactions": user.total_interactions
            }
        except SQLAlchemyError as e:
            db.rollback()
            log.error(f"❌ Ошибка добавления пользователя {user_id}: {e}")
            return {}
        finally:
            db.close()
    
    def log_interaction(self, user_id: int):
        """
        Логирует взаимодействие пользователя
        
        Args:
            user_id: ID пользователя
        """
        db = self._get_db()
        try:
            now = datetime.utcnow()
            date_str = now.strftime("%Y-%m-%d")
            
            # Обновляем счетчик в users
            user = db.query(User).filter(User.user_id == user_id).first()
            if user:
                user.total_interactions += 1
                user.last_interaction = now
            
            # Добавляем запись о взаимодействии
            interaction = Interaction(
                user_id=user_id,
                interaction_date=now,
                interaction_type='message'
            )
            db.add(interaction)
            
            db.commit()
        except SQLAlchemyError as e:
            db.rollback()
            log.error(f"❌ Ошибка логирования взаимодействия {user_id}: {e}")
        finally:
            db.close()
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """
        Получает данные пользователя
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Данные пользователя или None
        """
        db = self._get_db()
        try:
            user = db.query(User).filter(User.user_id == user_id).first()
            if not user:
                return None
            
            return {
                "user_id": user.user_id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "first_interaction": user.first_interaction.isoformat() if user.first_interaction else None,
                "last_interaction": user.last_interaction.isoformat() if user.last_interaction else None,
                "total_interactions": user.total_interactions
            }
        except SQLAlchemyError as e:
            log.error(f"❌ Ошибка получения пользователя {user_id}: {e}")
            return None
        finally:
            db.close()
    
    def get_all_users(self) -> List[Dict]:
        """Возвращает список всех пользователей"""
        db = self._get_db()
        try:
            users = db.query(User).all()
            return [
                {
                    "user_id": u.user_id,
                    "username": u.username,
                    "first_name": u.first_name,
                    "last_name": u.last_name,
                    "first_interaction": u.first_interaction.isoformat() if u.first_interaction else None,
                    "last_interaction": u.last_interaction.isoformat() if u.last_interaction else None,
                    "total_interactions": u.total_interactions
                }
                for u in users
            ]
        except SQLAlchemyError as e:
            log.error(f"❌ Ошибка получения всех пользователей: {e}")
            return []
        finally:
            db.close()
    
    def get_user_stats(self, user_id: int) -> Optional[Dict]:
        """
        Получает статистику пользователя
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Статистика или None
        """
        db = self._get_db()
        try:
            # Общее количество взаимодействий
            total = db.query(Interaction).filter(Interaction.user_id == user_id).count()
            
            # Статистика по датам
            interactions = db.query(Interaction).filter(Interaction.user_id == user_id).all()
            by_date = {}
            for interaction in interactions:
                date = interaction.interaction_date.strftime("%Y-%m-%d") if interaction.interaction_date else "unknown"
                by_date[date] = by_date.get(date, 0) + 1
            
            return {
                "total": total,
                "by_date": by_date
            }
        except SQLAlchemyError as e:
            log.error(f"❌ Ошибка получения статистики пользователя {user_id}: {e}")
            return None
        finally:
            db.close()
    
    def get_total_users(self) -> int:
        """Возвращает общее количество пользователей"""
        db = self._get_db()
        try:
            return db.query(User).count()
        except SQLAlchemyError as e:
            log.error(f"❌ Ошибка получения количества пользователей: {e}")
            return 0
        finally:
            db.close()
    
    def get_active_users_today(self) -> int:
        """Возвращает количество активных пользователей сегодня"""
        db = self._get_db()
        try:
            today = datetime.utcnow().date()
            # Считаем уникальных пользователей за сегодня
            from sqlalchemy import func, distinct, cast, Date
            count = db.query(func.count(distinct(Interaction.user_id))).filter(
                cast(Interaction.interaction_date, Date) == today
            ).scalar()
            return count or 0
        except SQLAlchemyError as e:
            log.error(f"❌ Ошибка получения активных пользователей: {e}")
            return 0
        finally:
            db.close()
    
    def get_stats(self) -> Dict:
        """Возвращает общую статистику"""
        db = self._get_db()
        try:
            total_users = db.query(User).count()
            total_interactions = db.query(Interaction).count()
            active_today = self.get_active_users_today()
            
            return {
                "total_users": total_users,
                "active_today": active_today,
                "total_interactions": total_interactions
            }
        except SQLAlchemyError as e:
            log.error(f"❌ Ошибка получения статистики: {e}")
            return {
                "total_users": 0,
                "active_today": 0,
                "total_interactions": 0
            }
        finally:
            db.close()
    
    def ban_user(self, user_id: int, reason: str = "", banned_by: int = None) -> bool:
        """
        Банит пользователя
        
        Args:
            user_id: ID пользователя для бана
            reason: Причина бана
            banned_by: ID администратора
        
        Returns:
            True если успешно, False если уже забанен
        """
        db = self._get_db()
        try:
            user = db.query(User).filter(User.user_id == user_id).first()
            if not user:
                # Создаем пользователя если его нет
                user = User(
                    user_id=user_id,
                    is_banned=True,
                    ban_reason=reason,
                    banned_at=datetime.utcnow(),
                    banned_by=banned_by
                )
                db.add(user)
            elif user.is_banned:
                return False  # Уже забанен
            else:
                user.is_banned = True
                user.ban_reason = reason
                user.banned_at = datetime.utcnow()
                user.banned_by = banned_by
            
            db.commit()
            log.info(f"🚫 Пользователь {user_id} забанен админом {banned_by}")
            return True
        except SQLAlchemyError as e:
            db.rollback()
            log.error(f"❌ Ошибка бана пользователя {user_id}: {e}")
            return False
        finally:
            db.close()
    
    def unban_user(self, user_id: int) -> bool:
        """
        Разбанивает пользователя
        
        Args:
            user_id: ID пользователя
        
        Returns:
            True если успешно
        """
        db = self._get_db()
        try:
            user = db.query(User).filter(User.user_id == user_id).first()
            if user and user.is_banned:
                user.is_banned = False
                user.ban_reason = None
                user.banned_at = None
                user.banned_by = None
                db.commit()
                log.info(f"✅ Пользователь {user_id} разбанен")
                return True
            return False
        except SQLAlchemyError as e:
            db.rollback()
            log.error(f"❌ Ошибка разбана пользователя {user_id}: {e}")
            return False
        finally:
            db.close()
    
    def is_banned(self, user_id: int) -> bool:
        """
        Проверяет, забанен ли пользователь
        
        Args:
            user_id: ID пользователя
        
        Returns:
            True если забанен
        """
        db = self._get_db()
        try:
            user = db.query(User).filter(User.user_id == user_id).first()
            return user.is_banned if user else False
        except SQLAlchemyError as e:
            log.error(f"❌ Ошибка проверки бана {user_id}: {e}")
            return False
        finally:
            db.close()
    
    def get_banned_users(self) -> List[Dict]:
        """Возвращает список всех забаненных пользователей"""
        db = self._get_db()
        try:
            users = db.query(User).filter(User.is_banned == True).all()
            return [
                {
                    "user_id": u.user_id,
                    "username": u.username,
                    "first_name": u.first_name,
                    "last_name": u.last_name,
                    "ban_reason": u.ban_reason,
                    "banned_at": u.banned_at.isoformat() if u.banned_at else None,
                    "banned_by": u.banned_by
                }
                for u in users
            ]
        except SQLAlchemyError as e:
            log.error(f"❌ Ошибка получения забаненных пользователей: {e}")
            return []
        finally:
            db.close()


class SessionDatabase:
    """База данных сессий пользователей (PostgreSQL)"""
    
    def __init__(self):
        """Инициализация базы данных"""
        log.info(f"📊 SessionDatabase: подключение к PostgreSQL")
    
    def _get_db(self) -> DBSession:
        """Создает новую сессию БД"""
        return SessionLocal()
    
    def save_session(self, session_key: str, session_data: Dict[str, Any]):
        """
        Сохраняет или обновляет сессию
        
        Args:
            session_key: Ключ сессии (user_id:thread_id)
            session_data: Данные сессии (messages, cart_products, session_id)
        """
        db = self._get_db()
        try:
            # Извлекаем user_id из session_key
            user_id = int(session_key.split(':')[0])
            
            # Извлекаем последние сообщения для читаемости
            messages = session_data.get("messages", [])
            last_user_msg = None
            last_bot_msg = None
            
            # Ищем последние сообщения пользователя и бота
            for msg in reversed(messages):
                if isinstance(msg, dict):
                    role = msg.get("role")
                    content = msg.get("content", "")
                    
                    if role == "user" and not last_user_msg:
                        last_user_msg = content[:500] if len(content) > 500 else content  # Обрезаем до 500 символов
                    elif role == "assistant" and not last_bot_msg:
                        last_bot_msg = content[:500] if len(content) > 500 else content
                    
                    if last_user_msg and last_bot_msg:
                        break
            
            # Ищем существующую сессию
            session = db.query(Session).filter(Session.session_key == session_key).first()
            
            if session:
                # Обновляем существующую
                session.messages = messages
                session.cart_products = session_data.get("cart_products", {})
                session.session_id = session_data.get("session_id")
                session.last_user_message = last_user_msg
                session.last_bot_message = last_bot_msg
                session.last_updated = datetime.utcnow()
            else:
                # Создаем новую
                session = Session(
                    session_key=session_key,
                    user_id=user_id,
                    messages=messages,
                    cart_products=session_data.get("cart_products", {}),
                    session_id=session_data.get("session_id"),
                    last_user_message=last_user_msg,
                    last_bot_message=last_bot_msg,
                    last_updated=datetime.utcnow()
                )
                db.add(session)
            
            db.commit()
        except SQLAlchemyError as e:
            db.rollback()
            log.error(f"❌ Ошибка сохранения сессии {session_key}: {e}")
        finally:
            db.close()
    
    def get_session(self, session_key: str) -> Optional[Dict[str, Any]]:
        """
        Получает данные сессии
        
        Args:
            session_key: Ключ сессии (user_id:thread_id)
        
        Returns:
            Данные сессии или None
        """
        db = self._get_db()
        try:
            session = db.query(Session).filter(Session.session_key == session_key).first()
            if not session:
                return None
            
            return {
                "messages": session.messages or [],
                "cart_products": session.cart_products or {},
                "session_id": session.session_id
            }
        except SQLAlchemyError as e:
            log.error(f"❌ Ошибка получения сессии {session_key}: {e}")
            return None
        finally:
            db.close()
    
    def delete_session(self, session_key: str):
        """
        Удаляет сессию
        
        Args:
            session_key: Ключ сессии (user_id:thread_id)
        """
        db = self._get_db()
        try:
            session = db.query(Session).filter(Session.session_key == session_key).first()
            if session:
                db.delete(session)
                db.commit()
                log.info(f"🗑️ Сессия {session_key} удалена")
        except SQLAlchemyError as e:
            db.rollback()
            log.error(f"❌ Ошибка удаления сессии {session_key}: {e}")
        finally:
            db.close()
    
    def get_user_sessions(self, user_id: int) -> List[str]:
        """
        Получает все ключи сессий для пользователя
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Список ключей сессий
        """
        db = self._get_db()
        try:
            sessions = db.query(Session).filter(Session.user_id == user_id).all()
            return [s.session_key for s in sessions]
        except SQLAlchemyError as e:
            log.error(f"❌ Ошибка получения сессий пользователя {user_id}: {e}")
            return []
        finally:
            db.close()
