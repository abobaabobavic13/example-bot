import logging

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError

from database import get_session, User, Admin
import keyboards
import config # To check BOT_ACTIVE_STATE_KEY
import utils # For admin check decorator and global state

logger = logging.getLogger(__name__)

# --- Start Command (Handles User Registration) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command, registers user if new."""
    user_data = update.effective_user
    user_id = user_data.id
    first_name = user_data.first_name
    username = user_data.username

    is_admin_user = False
    user_status = None

    try:
        async with get_session() as session:
            # Check if admin
            admin = await session.get(Admin, user_id)
            if admin:
                is_admin_user = True
                logger.info(f"Admin {user_id} ({username}) started the bot.")
                # Optionally load admin specific state here if needed
            else:
                 # Check if regular user exists, create if not
                user = await session.get(User, user_id)
                if not user:
                    user = User(
                        telegram_id=user_id,
                        first_name=first_name,
                        username=username,
                        is_active=True # Default to active on first start
                    )
                    session.add(user)
                    await session.flush() # Ensure user exists before accessing attributes
                    await session.commit() # Commit new user separately? Or let get_session handle commit
                    logger.info(f"New user registered: {user_id} ({username})")
                    await update.message.reply_text("Добро пожаловать! Вы зарегистрированы. Бот запущен.")
                    user_status = user.is_active
                else:
                    # Existing user
                    user_status = user.is_active
                    logger.info(f"User {user_id} ({username}) started the bot. Active: {user_status}")
                    await update.message.reply_text(f"С возвращением, {first_name}! Ваш текущий статус: {'Активен ✅' if user_status else 'Остановлен ❌'}")

            # Send appropriate keyboard
            if is_admin_user:
                bot_globally_active = utils.is_bot_globally_active(context)
                reply_markup = keyboards.get_admin_main_menu(bot_globally_active)
                await update.message.reply_text("Admin Menu:", reply_markup=reply_markup)
            elif user_status is not None: # Should always be true for non-admins after logic above
                 reply_markup = keyboards.get_user_main_menu(is_active=user_status)
                 await update.message.reply_text("User Menu:", reply_markup=reply_markup)

    except SQLAlchemyError as e:
        logger.error(f"Database error during /start for user {user_id}: {e}")
        await update.message.reply_text("Произошла ошибка базы данных при запуске. Попробуйте позже.")
    except Exception as e:
        logger.error(f"Unexpected error during /start for user {user_id}: {e}", exc_info=True)
        await update.message.reply_text("Произошла внутренняя ошибка. Попробуйте позже.")


# --- Help Command ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays help information based on user role."""
    user_id = update.effective_user.id
    is_admin_user = False

    try:
        async with get_session() as session:
            admin = await session.get(Admin, user_id)
            if admin:
                is_admin_user = True
    except SQLAlchemyError as e:
        logger.error(f"Database error checking admin status for help command (user {user_id}): {e}")
        # Proceed as non-admin or show error? Let's proceed as non-admin for now.

    if is_admin_user:
        help_text = """
        *Админ-команды:*
        - *✉️ Отправить фото*: Начать процесс отправки нового задания (фото) пользователям.
        - *🔴 Стоп Бот / 🟢 Старт Бот*: Приостановить / Возобновить рассылку заданий *всем* пользователям.
        - */global_stats*: Показать общую статистику по всем пользователям.
        - */help*: Показать это сообщение.

        *Модерация:*
        - Когда пользователь отвечает "Успешно", вы получите уведомление с кнопками "Подтвердить" / "Отклонить".
        - После "Подтвердить" можно "Отметить как слёт".
        - "Отменить слёт" можно в течение 5 минут после отметки.
        """
    else:
        help_text = """
        *Команды пользователя:*
        - *🟢 Запустить бота*: Начать получать задания от администраторов.
        - *🔴 Остановить бота*: Приостановить получение новых заданий.
        - */stats*: Показать вашу личную статистику (успешные номера, слёты).
        - */help*: Показать это сообщение.

        *Ответы на задания:*
        - Используйте кнопки "✅ Успешно" или "🔄 Повтор" под полученным фото-заданием.
        """

    await update.message.reply_text(help_text, parse_mode='Markdown')


# --- Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors caused by Updates."""
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)

    # Optionally, send a message to the user or a specific admin/group
    # if isinstance(context.error, BadRequest):
    #     # handle malformed requests - read more below!
    #     pass
    # elif isinstance(context.error, Forbidden):
    #     # handle forbidden errors - read more below!
    #     pass
    # Consider notifying admins for critical errors