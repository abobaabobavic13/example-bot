import bcrypt
import redis
import logging
from functools import wraps

from telegram.ext import ContextTypes

import config
import constants

logger = logging.getLogger(__name__)

# --- Password Hashing ---
def hash_password(password: str) -> bytes:
    """Hashes a password using bcrypt."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

def check_password(plain_password: str, hashed_password: bytes) -> bool:
    """Checks a plain password against a stored hash."""
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password)

# --- Redis Connection ---
redis_client = None
try:
    redis_client = redis.StrictRedis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        password=config.REDIS_PASSWORD,
        decode_responses=True # Decode responses to strings
    )
    redis_client.ping()
    logger.info("Successfully connected to Redis.")
except redis.exceptions.ConnectionError as e:
    logger.error(f"Could not connect to Redis: {e}")
    redis_client = None # Ensure client is None if connection fails

def get_redis_client():
    """Returns the Redis client instance."""
    if redis_client is None:
        logger.error("Redis client is not available.")
        # In a real app, you might want to raise an exception
        # or attempt to reconnect here.
    return redis_client

# --- Redis Timer Helpers ---
def set_slyot_cancel_timer(response_id: int):
    """Sets the 5-minute timer in Redis for slyot cancellation."""
    r = get_redis_client()
    if r:
        key = f"{constants.REDIS_SLYOT_CANCEL_KEY_PREFIX}{response_id}"
        try:
            r.setex(key, config.SLYOT_CANCEL_TIMEOUT_SECONDS, "active")
            logger.info(f"Set slyot cancel timer for response {response_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to set Redis key {key}: {e}")
    return False

def check_slyot_cancel_timer(response_id: int) -> bool:
    """Checks if the slyot cancellation timer is still active."""
    r = get_redis_client()
    if r:
        key = f"{constants.REDIS_SLYOT_CANCEL_KEY_PREFIX}{response_id}"
        try:
            return r.exists(key)
        except Exception as e:
            logger.error(f"Failed to check Redis key {key}: {e}")
    return False # Assume inactive if Redis error

def clear_slyot_cancel_timer(response_id: int):
    """Deletes the slyot cancellation timer key from Redis."""
    r = get_redis_client()
    if r:
        key = f"{constants.REDIS_SLYOT_CANCEL_KEY_PREFIX}{response_id}"
        try:
            r.delete(key)
            logger.info(f"Cleared slyot cancel timer for response {response_id}")
        except Exception as e:
            logger.error(f"Failed to delete Redis key {key}: {e}")

# --- Decorator for Admin Check ---
def admin_required(func):
    """Decorator to check if the user is a registered admin."""
    @wraps(func)
    async def wrapper(update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        from database import get_session, Admin # Import here to avoid circular dependency
        user_id = update.effective_user.id
        is_admin = False
        async with get_session() as session:
            admin = await session.get(Admin, user_id)
            if admin:
                is_admin = True

        if is_admin:
            return await func(update, context, *args, **kwargs)
        else:
            if update.callback_query:
                await update.callback_query.answer("Access denied. Admin privileges required.", show_alert=True)
            elif update.message:
                await update.message.reply_text("Access denied. Admin privileges required.")
            logger.warning(f"Unauthorized access attempt by user {user_id} to function {func.__name__}")
            return None # Or some specific return value indicating failure
    return wrapper

# --- Global Bot State Helper ---
def is_bot_globally_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Checks the global bot state flag."""
    # Defaults to True if not set
    return context.bot_data.get(config.BOT_ACTIVE_STATE_KEY, True)

def set_bot_globally_active(context: ContextTypes.DEFAULT_TYPE, active: bool):
    """Sets the global bot state flag."""
    context.bot_data[config.BOT_ACTIVE_STATE_KEY] = active
    logger.info(f"Global bot active state set to: {active}")