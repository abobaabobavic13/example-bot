from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

import constants

# --- Reply Keyboards ---

def get_user_main_menu(is_active: bool) -> ReplyKeyboardMarkup:
    """Gets the main reply keyboard for regular users."""
    if is_active:
        button_text = "🔴 Остановить бота"
    else:
        button_text = "🟢 Запустить бота"
    keyboard = [
        [KeyboardButton(button_text)],
        [KeyboardButton("/stats"), KeyboardButton("/help")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_admin_main_menu(is_bot_globally_active: bool) -> ReplyKeyboardMarkup:
    """Gets the main reply keyboard for admins."""
    global_status_text = "🔴 Стоп Бот (Глобально)" if is_bot_globally_active else "🟢 Старт Бот (Глобально)"
    keyboard = [
        [KeyboardButton("✉️ Отправить фото")],
        [KeyboardButton(global_status_text)],
        [KeyboardButton("/global_stats"), KeyboardButton("/help")],
        # Add more admin-specific commands if needed
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# --- Inline Keyboards ---

def get_user_task_response_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Keyboard for user to respond to a task."""
    keyboard = [
        [
            InlineKeyboardButton("✅ Успешно", callback_data=f"{constants.CB_USER_TASK_SUCCESS}_{task_id}"),
            # InlineKeyboardButton("🔄 Повтор", callback_data=f"{constants.CB_USER_TASK_REPEAT}_{task_id}"), # Enable if needed
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_moderation_keyboard(response_id: int) -> InlineKeyboardMarkup:
    """Keyboard for admin to confirm/reject a user's 'success' response."""
    keyboard = [
        [
            InlineKeyboardButton("👍 Подтвердить", callback_data=f"{constants.CB_ADMIN_CONFIRM}{response_id}"),
            InlineKeyboardButton("👎 Отклонить", callback_data=f"{constants.CB_ADMIN_REJECT}{response_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_slyot_keyboard(response_id: int, can_cancel: bool = False) -> InlineKeyboardMarkup:
    """Keyboard for admin after confirming, allowing 'Mark as Slyot' or 'Cancel Slyot'."""
    buttons = []
    if not can_cancel:
         # Default: Show "Mark as Slyot" after confirmation
         buttons.append(InlineKeyboardButton("🚩 Отметить как слёт", callback_data=f"{constants.CB_ADMIN_MARK_SLYOT}{response_id}"))
    else:
         # Show "Cancel Slyot" if within the timeout window
         buttons.append(InlineKeyboardButton("↩️ Отменить слёт (5 мин)", callback_data=f"{constants.CB_ADMIN_CANCEL_SLYOT}{response_id}"))

    # Optionally add a "Done" or similar button if needed
    # buttons.append(InlineKeyboardButton("👌 Готово", callback_data=f"admin_done_{response_id}"))

    keyboard = [buttons] if buttons else []
    return InlineKeyboardMarkup(keyboard)