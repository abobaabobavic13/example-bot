import logging

from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError

from database import get_session, User, Response, Task
import keyboards
import constants
import utils  # For global active check

logger = logging.getLogger(__name__)


# --- User Menu Button Handlers ---

async def toggle_user_bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles 'Запустить бота' / 'Остановить бота' button presses."""
    user_id = update.effective_user.id
    message_text = update.message.text

    should_be_active = "Запустить бота" in message_text  # If they pressed "Запустить", they want to be active

    new_status_text = ""
    reply_markup = None

    try:
        async with get_session() as session:
            user = await session.get(User, user_id)
            if not user:
                # Should not happen if /start worked, but handle defensively
                await update.message.reply_text("Не удалось найти ваш профиль. Попробуйте /start")
                logger.warning(f"User {user_id} pressed status toggle but not found in DB.")
                return

            if user.is_active == should_be_active:
                # User pressed the button reflecting the current state, do nothing
                status_now = 'Активен ✅' if user.is_active else 'Остановлен ❌'
                await update.message.reply_text(f"Бот уже в состоянии: {status_now}")
            else:
                user.is_active = should_be_active
                await session.commit()  # Commit the change
                status_now = 'Активен ✅' if user.is_active else 'Остановлен ❌'
                new_status_text = f"Статус обновлен: {status_now}"
                logger.info(f"User {user_id} set active status to: {should_be_active}")

            # Update the keyboard regardless
            reply_markup = keyboards.get_user_main_menu(is_active=user.is_active)
            await update.message.reply_text(new_status_text if new_status_text else "Ваш статус:",
                                            reply_markup=reply_markup)

    except SQLAlchemyError as e:
        logger.error(f"Database error toggling user {user_id} status: {e}")
        await update.message.reply_text("Ошибка базы данных при изменении статуса.")
    except Exception as e:
        logger.error(f"Unexpected error toggling user {user_id} status: {e}", exc_info=True)
        await update.message.reply_text("Произошла внутренняя ошибка.")


# --- User Task Response Handlers (CallbackQuery) ---

async def handle_user_task_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles 'Успешно'/'Повтор' button presses on tasks."""
    query = update.callback_query
    await query.answer()  # Acknowledge the button press

    user_id = query.from_user.id
    callback_data = query.data
    message = query.message  # The message containing the task photo and buttons

    try:
        action, task_id_str = callback_data.split('_', maxsplit=2)[
                              1:]  # e.g. user_task_success_123 -> ['task', 'success', '123'] -> split('user_') -> ['task_success_123'] -> split('_', 2) -> ['task', 'success', '123'] -> [1:] -> ['success', '123'] OR more robust: action = callback_data.split('_')[1], task_id = callback_data.split('_')[-1]
        action = f"{constants.CALLBACK_USER_ACTION_PREFIX}{action}"  # Reconstruct action like user_task_success
        task_id = int(task_id_str)

        async with get_session() as session:
            # Find the specific Response record for this user and task
            stmt = select(Response).where(Response.user_telegram_id == user_id, Response.task_id == task_id)
            result = await session.execute(stmt)
            response = result.scalar_one_or_none()

            if not response:
                await query.edit_message_text("Ошибка: Ответ не найден или уже обработан.")
                logger.warning(f"Response not found for user {user_id}, task {task_id}")
                return

            if response.status != 'pending_user':
                await query.edit_message_text(f"Вы уже ответили на это задание (Статус: {response.status}).")
                logger.warning(
                    f"User {user_id} tried to respond again to task {task_id}, current status: {response.status}")
                return

            # Find the task details (needed for admin notification)
            task = await session.get(Task, task_id)
            if not task:
                await query.edit_message_text("Ошибка: Задание не найдено.")
                logger.error(f"Task {task_id} referenced by response {response.id} not found!")
                return

            # Handle 'Успешно'
            if action == constants.CB_USER_TASK_SUCCESS:
                response.status = 'success_pending_admin'
                await session.commit()  # Commit status change first

                logger.info(f"User {user_id} marked task {task_id} as SUCCESS. Pending admin moderation.")

                # Notify ALL admins
                admin_stmt = select(Admin)
                admin_result = await session.execute(admin_stmt)
                admins = admin_result.scalars().all()

                user_info = query.from_user
                user_details = f"{user_info.first_name}" + (
                    f" (@{user_info.username})" if user_info.username else f" (ID: {user_id})")
                admin_message_text = f"🔔 Новый ответ 'Успешно' от {user_details} на Задание #{task_id}."
                admin_keyboard = keyboards.get_admin_moderation_keyboard(response.id)

                # Send notification with photo and buttons to each admin
                sent_to_admin = False
                for admin in admins:
                    try:
                        msg_to_admin = await context.bot.send_photo(
                            chat_id=admin.telegram_id,
                            photo=task.photo_file_id,
                            caption=admin_message_text,
                            reply_markup=admin_keyboard
                        )
                        # Store the first successfully sent admin message ID for potential future edits
                        if not response.moderation_message_id:
                            response.moderation_message_id = msg_to_admin.message_id
                            await session.commit()
                        sent_to_admin = True
                    except Exception as e:
                        logger.error(
                            f"Failed to send moderation notification for response {response.id} to admin {admin.telegram_id}: {e}")

                if sent_to_admin:
                    await query.edit_message_reply_markup(reply_markup=None)  # Remove buttons from user message
                    await context.bot.send_message(user_id, "Ваш ответ 'Успешно' отправлен на проверку администратору.")
                else:
                    # Revert status if failed to notify any admin? Maybe not, admin can check pending list later.
                    response.status = 'pending_user'  # Revert if failed? Needs consideration.
                    await session.commit()
                    await query.edit_message_text("Не удалось уведомить администраторов. Попробуйте позже.")
                    logger.error(f"Failed to notify ANY admin for response {response.id}")


            # Handle 'Повтор' (Example: just log and remove buttons)
            # elif action == constants.CB_USER_TASK_REPEAT:
            #     # Decide what 'Repeat' means. Log it? Mark response? Notify admin differently?
            #     # For now, let's just log it and maybe inform the user.
            #     response.status = 'repeat_requested' # Example status
            #     await session.commit()
            #     logger.info(f"User {user_id} requested REPEAT for task {task_id}.")
            #     await query.edit_message_reply_markup(reply_markup=None) # Remove buttons
            #     await context.bot.send_message(user_id, "Запрос на повтор получен.")
            #     # Optionally notify admin about the repeat request

            else:
                logger.warning(f"Unknown user action '{action}' received for task {task_id} from user {user_id}")
                await query.edit_message_text("Неизвестное действие.")


    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing callback data '{callback_data}': {e}")
        await query.edit_message_text("Ошибка обработки вашего ответа.")
    except SQLAlchemyError as e:
        logger.error(
            f"Database error handling user task response for user {user_id}, task {task_id_str if 'task_id_str' in locals() else 'N/A'}: {e}")
        await query.edit_message_text("Ошибка базы данных при обработке ответа.")
    except Exception as e:
        logger.error(f"Unexpected error handling user task response: {e}", exc_info=True)
        try:
            # Try sending a message instead of editing if edit fails
            await context.bot.send_message(user_id, "Произошла ошибка при обработке вашего ответа.")
        except Exception as send_e:
            logger.error(f"Failed to send error message to user {user_id}: {send_e}")


# --- Stats Command ---

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /stats command for users."""
    user_id = update.effective_user.id

    try:
        async with get_session() as session:
            user = await session.get(User, user_id)
            if not user:
                await update.message.reply_text("Не удалось найти ваш профиль. Попробуйте /start")
                return

            success_count = user.success_count
            fail_count = user.fail_count
            # Simple Rating Example: success - failures (can be more complex)
            rating = success_count - fail_count

            stats_text = f"""
            📊 *Ваша статистика:*

            - ✅ Успешных номеров: *{success_count}*
            - 🚩 Слётов (отклонено): *{fail_count}*

            - 🏆 Общий рейтинг активности: *{rating}*
            """
            # Send stats with the current user menu
            reply_markup = keyboards.get_user_main_menu(is_active=user.is_active)
            await update.message.reply_text(stats_text, parse_mode='Markdown', reply_markup=reply_markup)

    except SQLAlchemyError as e:
        logger.error(f"Database error fetching stats for user {user_id}: {e}")
        await update.message.reply_text("Ошибка базы данных при получении статистики.")
    except Exception as e:
        logger.error(f"Unexpected error fetching stats for user {user_id}: {e}", exc_info=True)
        await update.message.reply_text("Произошла внутренняя ошибка.")