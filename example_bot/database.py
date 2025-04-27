import datetime
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, BigInteger, ForeignKey, Text, LargeBinary
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

import config
import utils # For password hashing

logger = logging.getLogger(__name__)

# --- Database Setup ---
Base = declarative_base()

# Use async engine
try:
    engine = create_async_engine(config.DATABASE_URL, echo=False) # Set echo=True for debugging SQL
    # Async session factory
    AsyncSessionFactory = sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False, # Important for async usage
        autoflush=False,
        autocommit=False
    )
    logger.info(f"SQLAlchemy async engine created for {config.DATABASE_URL}")
except Exception as e:
    logger.error(f"Failed to create SQLAlchemy engine: {e}")
    engine = None
    AsyncSessionFactory = None

@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional scope around a series of operations."""
    if not AsyncSessionFactory:
        raise RuntimeError("Database session factory is not configured.")
    session = AsyncSessionFactory()
    try:
        yield session
        await session.commit()
    except Exception as e:
        logger.error(f"Session rollback initiated due to error: {e}")
        await session.rollback()
        raise
    finally:
        await session.close()

# --- Model Definitions ---

class User(Base):
    __tablename__ = 'users'
    telegram_id = Column(BigInteger, primary_key=True, index=True)
    first_name = Column(String, nullable=True)
    username = Column(String, nullable=True, index=True)
    is_active = Column(Boolean, default=True, nullable=False) # User's personal preference (start/stop bot)
    success_count = Column(Integer, default=0, nullable=False)
    fail_count = Column(Integer, default=0, nullable=False) # Includes rejected + slyots
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    responses = relationship("Response", back_populates="user")

    def __repr__(self):
        return f"<User(id={self.telegram_id}, name='{self.first_name}', active={self.is_active})>"


class Admin(Base):
    __tablename__ = 'admins'
    telegram_id = Column(BigInteger, primary_key=True, index=True)
    username = Column(String, nullable=True) # Store for reference
    password_hash = Column(LargeBinary, nullable=False) # Store hashed password as bytes
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    tasks = relationship("Task", back_populates="admin")

    def set_password(self, password: str):
        self.password_hash = utils.hash_password(password)

    def check_password(self, password: str) -> bool:
        return utils.check_password(password, self.password_hash)

    def __repr__(self):
        return f"<Admin(id={self.telegram_id}, username='{self.username}')>"


class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True)
    admin_telegram_id = Column(BigInteger, ForeignKey('admins.telegram_id'), nullable=False)
    photo_file_id = Column(String, nullable=False) # Telegram file_id of the photo
    # Optional: Add caption or description if needed
    # description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    admin = relationship("Admin", back_populates="tasks")
    responses = relationship("Response", back_populates="task") # One task can have many responses

    def __repr__(self):
        return f"<Task(id={self.id}, admin_id={self.admin_telegram_id})>"


class Response(Base):
    __tablename__ = 'responses'
    id = Column(Integer, primary_key=True)
    user_telegram_id = Column(BigInteger, ForeignKey('users.telegram_id'), nullable=False)
    task_id = Column(Integer, ForeignKey('tasks.id'), nullable=False)
    status = Column(String, default='pending_user', nullable=False, index=True) # e.g., 'pending_user', 'success_pending_admin', 'confirmed', 'rejected', 'slyot'
    moderation_message_id = Column(BigInteger, nullable=True) # Message ID sent to admin for moderation
    user_message_id = Column(BigInteger, nullable=True) # Message ID of the task sent to the user
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    user = relationship("User", back_populates="responses")
    task = relationship("Task", back_populates="responses")

    def __repr__(self):
        return f"<Response(id={self.id}, user_id={self.user_telegram_id}, task_id={self.task_id}, status='{self.status}')>"


# --- Database Initialization ---
async def init_db():
    """Creates database tables"""
    if not engine:
        logger.error("Cannot initialize DB: Engine not created.")
        return
    async with engine.begin() as conn:
        try:
            # await conn.run_sync(Base.metadata.drop_all) # Use carefully for development
            await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables created (if they didn't exist).")
            # Add initial admins if they don't exist
            async with get_session() as session:
                for admin_id in config.ADMIN_TELEGRAM_IDS:
                    existing_admin = await session.get(Admin, admin_id)
                    if not existing_admin:
                        admin = Admin(telegram_id=admin_id, username=f"InitialAdmin_{admin_id}") # Placeholder username
                        admin.set_password(config.ADMIN_PASSWORD)
                        session.add(admin)
                        logger.info(f"Added initial admin with ID: {admin_id}")
                await session.commit() # Commit within the get_session context manager handles this
        except Exception as e:
            logger.error(f"Error during DB initialization: {e}")