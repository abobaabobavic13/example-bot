import logging
import asyncio

import cose
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
    PicklePersistence, # Example persistence for bot_data
    Defaults
)
from telegram.constants import ParseMode

# Impnfiguration, constants, handlers, and database setup
import config
import constants
import database
import handlers_common
import handlers_user
import handlers_admin
import keyboards # For fallback message handler keyboard updates
import utils

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# Suppress overly verbose libraries if needed
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING) # Hide SQL queries unless debugging

logger = logging.getLogger(__name__)

# --- Persistence ---
# Use PicklePersistence to save bot_data (like the global active flag) across restarts
# NOTE: For production, consider a more robust persistence layer like Redis or DB for critical state.
persistence = PicklePersistence(filepath="bot_persistence.pickle")

# --- Main Function ---
async def main() -> None:
    """Start the bot."""
    logger.info("Starting bot...")

    # Initialize Database
    logger.info("Initializing database...")
    await database.init_db()
    logger.info("Database initialized.")

    # Check Redis Connection
    redis_client = utils.get_redis_client()
    if not redis_client:
        logger.warning("Redis is not connected. Slyot cancellation timer will not work.")
        # Decide if the bot should run without Redis or exit
        # exit("Exiting due to Redis connection failure.") # Uncomment to enforce Redis

    # Set default parse mode for messages
    bot_defaults = Defaults(parse_mode=ParseMode.MARKDOWN)

    # Create the Application and pass it your bot's token and persistence.
    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .persistence(persistence)
        .defaults(bot_defaults)
        .build()
    )

    # --- Handler Registration ---

    # Conversation Handlers first (if any complex flows)
    admin_login_handler = ConversationHandler(
        entry_points=[CommandHandler("admin_login", handlers_admin.admin_login_start)], # Example trigger
        states={
            constants.ADMIN_LOGIN_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers_admin.admin_login_password)],
        },
        fallbacks=[CommandHandler("cancel", handlers_admin.cancel_login)],
        persistent=False # Login state should not persist across restarts usually
    )

    admin_send_photo_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^✉️ Отправить фото$') & filters.ChatType.PRIVATE, handlers_admin.send_photo_start)],
        states={
            constants.ADMIN_SEND_PHOTO_CONFIRM: [
                MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handlers_admin.send_photo_receive),
                CommandHandler("send_task", handlers_admin.send_photo_execute) # Command to confirm sending
            ],
        },
         fallbacks=[CommandHandler("cancel", handlers_admin.cancel_send_photo),
                   MessageHandler(filters.COMMAND | filters.TEXT, handlers_admin.cancel_send_photo)], # Cancel on any other text/command
         persistent=False
    )

    # Order matters: Conversation Handlers should often come before general MessageHandlers

    # --- Admin Handlers ---
    application.add_handler(admin_login_handler) # Add login conversation
    application.add_handler(admin_send_photo_handler) # Add send photo conversation
    application.add_handler(CommandHandler("global_stats", handlers_admin.global_stats))
    # Handle admin menu buttons (that are not conversation entries)
    application.add_handler(MessageHandler(filters.Regex(r'^(🔴 Стоп Бот|🟢 Старт Бот)') & filters.ChatType.PRIVATE, handlers_admin.toggle_global_bot_status))

    # Admin CallbackQuery Handlers (Moderation & Slyot)
    application.add_handler(CallbackQueryHandler(handlers_admin.handle_admin_moderation, pattern=f"^{constants.CALLBACK_ADMIN_MODERATE_PREFIX}"))
    application.add_handler(CallbackQueryHandler(handlers_admin.handle_admin_slyot_action, pattern=f"^{constants.CALLBACK_ADMIN_SLYOT_PREFIX}"))

    # --- User Handlers ---
    application.add_handler(CommandHandler("stats", handlers_user.stats))
    # Handle user menu buttons
    application.add_handler(MessageHandler(filters.Regex(r'^(🔴 Остановить бота|🟢 Запустить бота)') & filters.ChatType.PRIVATE, handlers_user.toggle_user_bot_status))

    # User CallbackQuery Handler (Task Response)
    application.add_handler(CallbackQueryHandler(handlers_user.handle_user_task_response, pattern=f"^{constants.CALLBACK_USER_ACTION_PREFIX}task_")) # Pattern matches task success/repeat


    # --- Common Handlers ---
    application.add_handler(CommandHandler("start", handlers_common.start))
    application.add_handler(CommandHandler("help", handlers_common.help_command))

    # Generic message handler (for unrecognized commands or text) - Keep last
    # async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    #     # Check if admin or user to provide correct keyboard again
    #     # This part needs refinement based on how you want to handle unknown input
    #     # Maybe just ignore or provide help message
    #     await update.message.reply_text("Извините, я не понимаю эту команду. Используйте /help для списка команд.")
    # application.add_handler(MessageHandler(filters.COMMAND | filters.TEXT & filters.ChatType.PRIVATE, unknown))


    # --- Error Handler ---
    application.add_error_handler(handlers_common.error_handler)


    # --- Start Bot ---
    logger.info("Starting polling...")
    # Run the bot until the user presses Ctrl-C
    # Use run_polling for simplicity, or run_webhook for production deployment
    await application.initialize() # Initialize bot_data etc.
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    # Keep the script running
    # await application.idle() # Use this if your main logic doesn't block

    # Keep script running until interrupted
    stop_event = asyncio.Event()
    await stop_event.wait() # Keeps the main coroutine alive

    # --- Shutdown --- (This part might not be reached easily with start_polling + wait)
    # logger.info("Shutting down bot...")
    # await application.updater.stop()
    # await application.stop()
    # await application.shutdown()
    # logger.info("Bot shut down gracefully.")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
    except Exception as e:
         logger.critical(f"Critical error in main execution: {e}", exc_info=True)