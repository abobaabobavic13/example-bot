import logging

from sqlalchemy.orm import selectinload
from telegram import Update, InputMediaPhoto
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, Filters, CallbackQueryHandler
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError, Forbidden
from sqlalchemy.future import select
from sqlalchemy import func, and_
from sqlalchemy.exc import SQLAlchemyError

from database import get_session, User, Admin, Task, Response
import keyboards
import constants
import utils # For admin_required decorator, Redis, global state

logger = logging.getLogger(__name__)

# --- Admin Authentication (Example using ConversationHandler) ---
# This is a simple example. For production, consider more robust session management.

async def admin_login_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the admin login process."""
    user_id = update.effective_user.id
    # Basic check: Is the user ID *potentially* an admin based on config?
    # A more robust check involves checking the DB immediately if possible,
    # but this flow assumes we ask for password first based on ID list.
    # Or, better, just use @utils.admin_required on the command itself.

    # Let's assume /admin command is protected by @utils.admin_required
    # So if we reach here, the user IS an admin in the DB.
    # But we might still want a password check for certain actions or sessions.
    # For this example, let's skip the password step if already verified as admin.

    # Simplified: If the command is protected, just show the menu.
    # await update.message.reply_text("Admin access granted.") # Or directly show menu
    # return ConversationHandler.END # End if no password needed

    # --- If password check IS desired ---
    # Check if already logged in via context (simple session)
    if context.user_data.get('is_admin_logged_in'):
        await update.message.reply_text("Вы уже вошли как администратор.")
        bot_globally_active = utils.is_bot_globally_active(context)
        reply_markup = keyboards.get_admin_main_menu(bot_globally_active)
        await update.message.reply_text("Admin Menu:", reply_markup=reply_markup)
        return ConversationHandler.END

    await update.message.reply_text("Введите пароль администратора:")
    return constants.ADMIN_LOGIN_PASSWORD


async def admin_login_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives and checks the admin password."""
    user_id = update.effective_user.id
    password_attempt = update.message.text

    try:
        async with get_session() as session:
            admin = await session.get(Admin, user_id)
            if admin and admin.check_password(password_attempt):
                context.user_data['is_admin_logged_in'] = True # Simple session flag
                logger.info(f"Admin {user_id} successfully logged in.")
                await update.message.reply_text("Пароль верный. Доступ предоставлен.")
                bot_globally_active = utils.is_bot_globally_active(context)
                reply_markup = keyboards.get_admin_main_menu(bot_globally_active)
                await update.message.reply_text("Admin Menu:", reply_markup=reply_markup)
                return ConversationHandler.END
            else:
                logger.warning(f"Admin login failed for user {user_id}.")
                await update.message.reply_text("Неверный пароль. Попробуйте еще раз или /cancel.")
                return constants.ADMIN_LOGIN_PASSWORD # Stay in password state

    except SQLAlchemyError as e:
        logger.error(f"Database error during admin login for user {user_id}: {e}")
        await update.message.reply_text("Ошибка базы данных при проверке пароля.")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Unexpected error during admin login {user_id}: {e}", exc_info=True)
        await update.message.reply_text("Произошла внутренняя ошибка.")
        return ConversationHandler.END

async def cancel_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the admin login process."""
    await update.message.reply_text("Вход отменен.")
    context.user_data.pop('is_admin_logged_in', None) # Clear flag on cancel
    return ConversationHandler.END

# --- Send Photo Task ---
# Using ConversationHandler for multi-step process (send photo -> confirm)

@utils.admin_required
async def send_photo_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the process of sending a new photo task."""
    await update.message.reply_text("Пожалуйста, отправьте фото для нового задания. /cancel для отмены.")
    return constants.ADMIN_SEND_PHOTO_CONFIRM

@utils.admin_required
async def send_photo_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the photo from the admin."""
    if not update.message.photo:
        await update.message.reply_text("Пожалуйста, отправьте именно фото. /cancel для отмены.")
        return constants.ADMIN_SEND_PHOTO_CONFIRM

    # Get the highest resolution photo
    photo_file = update.message.photo[-1]
    context.user_data['task_photo_file_id'] = photo_file.file_id
    # context.user_data['task_caption'] = update.message.caption # Optional: store caption if needed

    # Ask for confirmation (optional, but good practice)
    await update.message.reply_text(f"Фото получено. Отправить это задание всем активным пользователям?\nНажмите /send_task для подтверждения или /cancel для отмены.")
    # Alternative: Use inline keyboard for confirmation
    # keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Отправить", callback_data="confirm_send_task"),
    #                                  InlineKeyboardButton("❌ Отмена", callback_data="cancel_send_task")]])
    # await update.message.reply_photo(photo_file.file_id, caption="Отправить это задание?", reply_markup=keyboard)

    return constants.ADMIN_SEND_PHOTO_CONFIRM # Stay in this state until /send_task or /cancel


@utils.admin_required
async def send_photo_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Confirms and sends the photo task to active users."""
    admin_id = update.effective_user.id
    photo_file_id = context.user_data.get('task_photo_file_id')

    if not photo_file_id:
        await update.message.reply_text("Ошибка: Фото не найдено. Пожалуйста, начните сначала, отправив фото.")
        return ConversationHandler.END

    if not utils.is_bot_globally_active(context):
         await update.message.reply_text("⚠️ Бот глобально остановлен. Задания не будут отправлены. Запустите бота через меню.")
         return ConversationHandler.END

    sent_count = 0
    failed_count = 0
    new_task_id = None

    try:
        async with get_session() as session:
            # 1. Create the Task record
            new_task = Task(
                admin_telegram_id=admin_id,
                photo_file_id=photo_file_id
                # description=context.user_data.get('task_caption') # Optional
            )
            session.add(new_task)
            await session.flush() # Get the new_task.id
            new_task_id = new_task.id

            # 2. Find active users
            stmt = select(User).where(User.is_active == True)
            result = await session.execute(stmt)
            active_users = result.scalars().all()

            if not active_users:
                await update.message.reply_text("Нет активных пользователей для отправки задания.")
                # Should we still save the task? Yes, probably.
                await session.commit()
                return ConversationHandler.END

            await update.message.reply_text(f"Начинаю отправку задания #{new_task_id} для {len(active_users)} активных пользователей...")

            # 3. Send to each active user and create Response record
            for user in active_users:
                user_keyboard = keyboards.get_user_task_response_keyboard(new_task_id)
                try:
                    msg_to_user = await context.bot.send_photo(
                        chat_id=user.telegram_id,
                        photo=photo_file_id,
                        # caption=f"Новое задание #{new_task_id}", # Optional caption
                        reply_markup=user_keyboard
                    )
                    # Create a response entry for this user and task
                    response = Response(
                        user_telegram_id=user.telegram_id,
                        task_id=new_task_id,
                        status='pending_user', # Initial status
                        user_message_id=msg_to_user.message_id
                    )
                    session.add(response)
                    sent_count += 1
                    # Avoid committing inside loop for performance, commit at the end
                except (BadRequest, Forbidden) as e: # Handle cases where user blocked the bot or chat not found
                    logger.warning(f"Failed to send task {new_task_id} to user {user.telegram_id}: {e}. Marking user inactive.")
                    # Optional: Mark user as inactive in DB if blocked
                    user.is_active = False
                    failed_count += 1
                except TelegramError as e:
                    logger.error(f"Telegram error sending task {new_task_id} to user {user.telegram_id}: {e}")
                    failed_count += 1
                except Exception as e:
                    logger.error(f"Unexpected error sending task {new_task_id} to user {user.telegram_id}: {e}", exc_info=True)
                    failed_count += 1

            await session.commit() # Commit all new responses (and potentially user status changes)
            logger.info(f"Task {new_task_id} sent by admin {admin_id}. Sent: {sent_count}, Failed: {failed_count}")
            await update.message.reply_text(f"Задание #{new_task_id} отправлено.\nУспешно: {sent_count}\nНе удалось: {failed_count}")

    except SQLAlchemyError as e:
        logger.error(f"Database error sending task from admin {admin_id}: {e}")
        await update.message.reply_text("Ошибка базы данных при отправке задания.")
    except Exception as e:
        logger.error(f"Unexpected error sending task from admin {admin_id}: {e}", exc_info=True)
        await update.message.reply_text("Произошла внутренняя ошибка при отправке задания.")
    finally:
        # Clean up context
        context.user_data.pop('task_photo_file_id', None)
        context.user_data.pop('task_caption', None)
        return ConversationHandler.END # End the conversation


@utils.admin_required
async def cancel_send_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the send photo process."""
    context.user_data.pop('task_photo_file_id', None)
    context.user_data.pop('task_caption', None)
    await update.message.reply_text("Отправка задания отменена.")
    return ConversationHandler.END


# --- Admin Moderation Handlers (CallbackQuery) ---

@utils.admin_required
async def handle_admin_moderation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles 'Подтвердить'/'Отклонить' callbacks from admins."""
    query = update.callback_query
    await query.answer()

    admin_id = query.from_user.id
    callback_data = query.data

    try:
        prefix, action, response_id_str = callback_data.split('_', maxsplit=2)
        response_id = int(response_id_str)
        action = f"{prefix}_{action}_" # Reconstruct action prefix like admin_mod_confirm_

        async with get_session() as session:
            # Fetch the response and related user
            # Use joinedload to efficiently fetch related user data
            stmt = select(Response).options(selectinload(Response.user)).where(Response.id == response_id)
            result = await session.execute(stmt)
            response = result.scalar_one_or_none()

            if not response:
                await query.edit_message_caption(caption="Ошибка: Ответ не найден (возможно, уже обработан).", reply_markup=None)
                logger.warning(f"Admin {admin_id} tried to moderate non-existent/processed response {response_id}")
                return

            user = response.user # Get user from relationship
            if not user:
                 await query.edit_message_caption(caption="Ошибка: Пользователь для этого ответа не найден.", reply_markup=None)
                 logger.error(f"User not found for response {response_id} (user_id {response.user_telegram_id})")
                 return

            # Prevent double moderation
            if response.status not in ['success_pending_admin']:
                 await query.edit_message_caption(caption=f"Этот ответ уже обработан (Статус: {response.status})", reply_markup=None)
                 logger.warning(f"Admin {admin_id} tried to moderate already processed response {response_id}, status: {response.status}")
                 return

            admin_user = query.from_user # Admin who clicked the button
            admin_info = f"{admin_user.first_name}" + (f" (@{admin_user.username})" if admin_user.username else f" ID: {admin_id}")
            user_info = f"{user.first_name}" + (f" (@{user.username})" if user.username else f" ID: {user.telegram_id}")

            # --- Handle Confirmation ---
            if action == constants.CB_ADMIN_CONFIRM:
                response.status = 'confirmed'
                user.success_count += 1
                await session.commit() # Commit changes

                logger.info(f"Admin {admin_id} CONFIRMED response {response_id} for user {user.telegram_id} (Task {response.task_id})")

                # Edit the admin notification message
                new_caption = f"✅ Подтверждено (Админ: {admin_info})\nОтвет от {user_info} на Задание #{response.task_id}."
                slyot_keyboard = keyboards.get_admin_slyot_keyboard(response_id, can_cancel=False)
                try:
                    await query.edit_message_caption(caption=new_caption, reply_markup=slyot_keyboard)
                except BadRequest as e:
                     if "message is not modified" in str(e): pass # Ignore if message didn't change
                     else: raise e
                except TelegramError as e:
                    logger.error(f"Error editing admin message {query.message.message_id} after confirming response {response_id}: {e}")
                    # Send a new message if edit failed
                    await context.bot.send_message(admin_id, f"✅ Ответ {response_id} подтвержден. Не удалось обновить исходное сообщение.")


                # Notify the user
                try:
                    await context.bot.send_message(user.telegram_id, f"👍 Ваш ответ на Задание #{response.task_id} подтвержден администратором!")
                except TelegramError as e:
                     logger.error(f"Failed to send confirmation notification to user {user.telegram_id} for response {response_id}: {e}")


            # --- Handle Rejection ---
            elif action == constants.CB_ADMIN_REJECT:
                response.status = 'rejected'
                user.fail_count += 1 # Increment fail count for rejection
                await session.commit()

                logger.info(f"Admin {admin_id} REJECTED response {response_id} for user {user.telegram_id} (Task {response.task_id})")

                # Edit the admin notification message
                new_caption = f"❌ Отклонено (Админ: {admin_info})\nОтвет от {user_info} на Задание #{response.task_id}."
                try:
                    await query.edit_message_caption(caption=new_caption, reply_markup=None) # Remove buttons
                except BadRequest as e:
                     if "message is not modified" in str(e): pass
                     else: raise e
                except TelegramError as e:
                    logger.error(f"Error editing admin message {query.message.message_id} after rejecting response {response_id}: {e}")
                    await context.bot.send_message(admin_id, f"❌ Ответ {response_id} отклонен. Не удалось обновить исходное сообщение.")

                # Notify the user
                try:
                    await context.bot.send_message(user.telegram_id, f"👎 К сожалению, ваш ответ на Задание #{response.task_id} был отклонен администратором.")
                except TelegramError as e:
                    logger.error(f"Failed to send rejection notification to user {user.telegram_id} for response {response_id}: {e}")

            else:
                 logger.warning(f"Unknown admin moderation action '{action}' received for response {response_id} from admin {admin_id}")
                 # Don't edit message if action is unknown


    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing admin moderation callback data '{callback_data}': {e}")
        await query.edit_message_caption(caption="Ошибка обработки команды.", reply_markup=None)
    except SQLAlchemyError as e:
        logger.error(f"Database error handling admin moderation for response {response_id_str if 'response_id_str' in locals() else 'N/A'}: {e}")
        await query.edit_message_caption(caption="Ошибка базы данных при модерации.", reply_markup=None)
    except Exception as e:
        logger.error(f"Unexpected error handling admin moderation: {e}", exc_info=True)
        try:
            await query.edit_message_caption(caption="Произошла внутренняя ошибка.", reply_markup=None)
        except Exception as edit_e:
             logger.error(f"Failed to edit message on unexpected error: {edit_e}")


# --- Slyot Handling (CallbackQuery) ---

@utils.admin_required
async def handle_admin_slyot_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles 'Отметить как слёт' and 'Отменить слёт' callbacks."""
    query = update.callback_query
    await query.answer()

    admin_id = query.from_user.id
    callback_data = query.data

    try:
        prefix, action_type, response_id_str = callback_data.split('_', maxsplit=2)
        response_id = int(response_id_str)
        action = f"{prefix}_{action_type}_" # Reconstruct action prefix

        async with get_session() as session:
            stmt = select(Response).options(selectinload(Response.user)).where(Response.id == response_id)
            result = await session.execute(stmt)
            response = result.scalar_one_or_none()

            if not response:
                await query.edit_message_caption(caption="Ошибка: Ответ не найден.", reply_markup=None)
                return

            user = response.user
            if not user:
                 await query.edit_message_caption(caption="Ошибка: Пользователь для этого ответа не найден.", reply_markup=None)
                 return

            admin_user = query.from_user
            admin_info = f"{admin_user.first_name}" + (f" (@{admin_user.username})" if admin_user.username else f" ID: {admin_id}")
            user_info = f"{user.first_name}" + (f" (@{user.username})" if user.username else f" ID: {user.telegram_id}")
            base_caption = f"Ответ от {user_info} на Задание #{response.task_id}."


            # --- Mark as Slyot ---
            if action == constants.CB_ADMIN_MARK_SLYOT:
                if response.status != 'confirmed':
                    await query.edit_message_caption(caption=f"{base_caption}\nНельзя отметить как слёт, статус не 'confirmed' (текущий: {response.status})", reply_markup=None)
                    return

                response.status = 'slyot'
                user.success_count -= 1 # Revert previous success increment
                user.fail_count += 1   # Increment fail count for slyot
                await session.commit()

                # Start Redis timer
                timer_set = utils.set_slyot_cancel_timer(response_id)
                if not timer_set:
                    logger.error(f"Failed to set Redis slyot cancel timer for response {response_id}")
                    # Inform admin, but proceed with status change
                    await context.bot.send_message(admin_id, f"⚠️ Не удалось запустить таймер отмены слёта для ответа {response_id}. Redis недоступен?")

                logger.info(f"Admin {admin_id} marked response {response_id} as SLYOT for user {user.telegram_id}")

                # Edit admin message
                new_caption = f"🚩 Отмечено как СЛЁТ (Админ: {admin_info})\n{base_caption}"
                cancel_keyboard = keyboards.get_admin_slyot_keyboard(response_id, can_cancel=True) # Show cancel button
                try:
                    await query.edit_message_caption(caption=new_caption, reply_markup=cancel_keyboard)
                except TelegramError as e:
                    logger.error(f"Error editing admin message {query.message.message_id} after marking slyot {response_id}: {e}")
                    await context.bot.send_message(admin_id, f"🚩 Ответ {response_id} отмечен как слёт. Не удалось обновить сообщение.")

                # Notify user
                try:
                    await context.bot.send_message(user.telegram_id, f"🚩 Ваш ранее подтвержденный ответ на Задание #{response.task_id} был помечен администратором как 'слёт' (ошибка). Ваша статистика обновлена.")
                except TelegramError as e:
                     logger.error(f"Failed to send slyot notification to user {user.telegram_id} for response {response_id}: {e}")


            # --- Cancel Slyot ---
            elif action == constants.CB_ADMIN_CANCEL_SLYOT:
                if response.status != 'slyot':
                     await query.edit_message_caption(caption=f"{base_caption}\nНельзя отменить слёт, статус не 'slyot' (текущий: {response.status})", reply_markup=None)
                     return

                # Check Redis timer
                if utils.check_slyot_cancel_timer(response_id):
                    utils.clear_slyot_cancel_timer(response_id) # Clear the timer

                    response.status = 'confirmed' # Revert status back to confirmed
                    user.fail_count -= 1     # Revert fail increment
                    user.success_count += 1  # Re-increment success count
                    await session.commit()

                    logger.info(f"Admin {admin_id} CANCELED SLYOT for response {response_id} (user {user.telegram_id})")

                    # Edit admin message back
                    new_caption = f"✅ Слёт Отменен (Админ: {admin_info})\n{base_caption}\nСтатус восстановлен: Подтверждено."
                    # Show 'Mark as Slyot' button again? Or just confirmation? Let's show Mark again.
                    slyot_keyboard = keyboards.get_admin_slyot_keyboard(response_id, can_cancel=False)
                    try:
                         await query.edit_message_caption(caption=new_caption, reply_markup=slyot_keyboard)
                    except TelegramError as e:
                        logger.error(f"Error editing admin message {query.message.message_id} after canceling slyot {response_id}: {e}")
                        await context.bot.send_message(admin_id, f"✅ Слёт для ответа {response_id} отменен. Не удалось обновить сообщение.")

                    # Notify user
                    try:
                        await context.bot.send_message(user.telegram_id, f"👍 Администратор отменил пометку 'слёт' для вашего ответа на Задание #{response.task_id}. Статистика восстановлена.")
                    except TelegramError as e:
                        logger.error(f"Failed to send cancel slyot notification to user {user.telegram_id} for response {response_id}: {e}")

                else:
                    # Timer expired or Redis failed
                    logger.warning(f"Admin {admin_id} tried to cancel slyot for response {response_id}, but timer expired or Redis unavailable.")
                    await query.edit_message_caption(
                        caption=f"❌ Не удалось отменить слёт (Админ: {admin_info})\n{base_caption}\nВозможно, истекло 5 минут или Redis недоступен.",
                        reply_markup=None # Remove cancel button if expired
                    )
            else:
                 logger.warning(f"Unknown admin slyot action '{action}' received for response {response_id} from admin {admin_id}")


    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing admin slyot callback data '{callback_data}': {e}")
        await query.edit_message_caption(caption="Ошибка обработки команды.", reply_markup=None)
    except SQLAlchemyError as e:
        logger.error(f"Database error handling admin slyot action for response {response_id_str if 'response_id_str' in locals() else 'N/A'}: {e}")
        await query.edit_message_caption(caption="Ошибка базы данных.", reply_markup=None)
    except Exception as e:
        logger.error(f"Unexpected error handling admin slyot action: {e}", exc_info=True)
        try:
            await query.edit_message_caption(caption="Произошла внутренняя ошибка.", reply_markup=None)
        except Exception as edit_e:
             logger.error(f"Failed to edit message on unexpected error: {edit_e}")


# --- Global Bot Control ---

@utils.admin_required
async def toggle_global_bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles 'Стоп Бот (Глобально)' / 'Старт Бот (Глобально)' button."""
    is_currently_active = utils.is_bot_globally_active(context)
    new_state = not is_currently_active
    utils.set_bot_globally_active(context, new_state)

    status_text = "🟢 ЗАПУЩЕН (Глобально)" if new_state else "🔴 ОСТАНОВЛЕН (Глобально)"
    user_alert = "Бот возобновил работу и скоро начнет отправлять задания." if new_state else "Бот временно приостановлен администратором. Новые задания отправляться не будут."

    logger.info(f"Admin {update.effective_user.id} set global bot status to {new_state}")

    # Update admin's keyboard
    reply_markup = keyboards.get_admin_main_menu(is_bot_globally_active=new_state)
    await update.message.reply_text(f"Статус бота изменен: {status_text}", reply_markup=reply_markup)

    # Optional: Notify all users about the change? Be careful with mass notifications.
    # Consider notifying only active users.
    # async with get_session() as session:
    #     stmt = select(User.telegram_id).where(User.is_active == True)
    #     result = await session.execute(stmt)
    #     active_user_ids = result.scalars().all()
    #     for user_id in active_user_ids:
    #         try:
    #             await context.bot.send_message(user_id, f"ℹ️ Внимание! {user_alert}")
    #         except Exception as e:
    #             logger.warning(f"Failed to send global status update to user {user_id}: {e}")


# --- Global Statistics ---

@utils.admin_required
async def global_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows aggregated statistics for all users."""
    admin_id = update.effective_user.id
    logger.info(f"Admin {admin_id} requested global stats.")

    try:
        async with get_session() as session:
            # Total users
            total_users_count = await session.scalar(select(func.count(User.telegram_id)))
            # Active users (user preference)
            active_users_count = await session.scalar(select(func.count(User.telegram_id)).where(User.is_active == True))
            # Total successes and failures across all users
            total_success = await session.scalar(select(func.sum(User.success_count))) or 0
            total_fails = await session.scalar(select(func.sum(User.fail_count))) or 0
            # Total tasks sent
            total_tasks = await session.scalar(select(func.count(Task.id))) or 0
            # Responses pending admin moderation
            pending_moderation = await session.scalar(select(func.count(Response.id)).where(Response.status == 'success_pending_admin')) or 0

            bot_globally_active = utils.is_bot_globally_active(context)
            global_status_text = "🟢 Активен" if bot_globally_active else "🔴 Остановлен"


            stats_text = f"""
            🌐 *Глобальная статистика бота:*

            - Статус бота: *{global_status_text}*
            - Всего пользователей: *{total_users_count}*
            - Активных пользователей (готовы получать задания): *{active_users_count}*
            - Всего заданий отправлено: *{total_tasks}*
            ---
            *Статистика по ответам:*
            - Всего успешных (подтверждено): *{total_success}*
            - Всего слётов/отклонено: *{total_fails}*
            - Ожидают модерации ('Успешно'): *{pending_moderation}*
            """

            await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)

    except SQLAlchemyError as e:
        logger.error(f"Database error fetching global stats for admin {admin_id}: {e}")
        await update.message.reply_text("Ошибка базы данных при получении глобальной статистики.")
    except Exception as e:
        logger.error(f"Unexpected error fetching global stats for admin {admin_id}: {e}", exc_info=True)
        await update.message.reply_text("Произошла внутренняя ошибка.")