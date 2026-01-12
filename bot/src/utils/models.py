"""
SQLAlchemy models for PostgreSQL database
"""
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, DateTime, Boolean, JSON, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

Base = declarative_base()


class User(Base):
    """User model"""
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    first_interaction = Column(DateTime, default=datetime.utcnow)
    last_interaction = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    total_interactions = Column(Integer, default=0)
    is_banned = Column(Boolean, default=False, index=True)
    ban_reason = Column(Text, nullable=True)
    banned_at = Column(DateTime, nullable=True)
    banned_by = Column(BigInteger, nullable=True)


class Session(Base):
    """User session model"""
    __tablename__ = 'sessions'
    
    id = Column(Integer, primary_key=True)
    session_key = Column(String(255), unique=True, nullable=False, index=True)  # user_id:thread_id
    user_id = Column(BigInteger, nullable=False, index=True)
    thread_id = Column(Integer, default=0)
    messages = Column(JSON, nullable=False, default=list)
    cart_products = Column(JSON, nullable=False, default=dict)
    session_id = Column(String(255), nullable=False)  # UUID for Langfuse
    last_user_message = Column(Text, nullable=True)  # Последнее сообщение пользователя (читаемо)
    last_bot_message = Column(Text, nullable=True)  # Последний ответ бота (читаемо)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class Interaction(Base):
    """User interaction log"""
    __tablename__ = 'interactions'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    interaction_date = Column(DateTime, default=datetime.utcnow, index=True)
    interaction_type = Column(String(50), default='message')  # message, voice, photo


def get_engine(database_url: str = None):
    """Create database engine"""
    if not database_url:
        database_url = os.getenv('DATABASE_URL', 'sqlite:///./db/vkusvill.db')
    
    return create_engine(database_url, echo=False)


def init_db(database_url: str = None):
    """Initialize database tables"""
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    return engine


def get_session(engine):
    """Get database session"""
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()

