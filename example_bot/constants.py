# Conversation Handler States
ADMIN_LOGIN_PASSWORD, ADMIN_MENU = range(2)
ADMIN_SEND_PHOTO_CONFIRM = range(1)

# Callback Data Prefixes - Use these to route callback queries
CALLBACK_USER_ACTION_PREFIX = "user_"
CALLBACK_ADMIN_MODERATE_PREFIX = "admin_mod_"
CALLBACK_ADMIN_SLYOT_PREFIX = "admin_slyot_"

# Callback Data Actions (examples)
# User Actions
CB_USER_START_BOT = f"{CALLBACK_USER_ACTION_PREFIX}start"
CB_USER_STOP_BOT = f"{CALLBACK_USER_ACTION_PREFIX}stop"
CB_USER_TASK_SUCCESS = f"{CALLBACK_USER_ACTION_PREFIX}task_success"
CB_USER_TASK_REPEAT = f"{CALLBACK_USER_ACTION_PREFIX}task_repeat"

# Admin Moderation Actions (suffix will be response_id)
CB_ADMIN_CONFIRM = f"{CALLBACK_ADMIN_MODERATE_PREFIX}confirm_"
CB_ADMIN_REJECT = f"{CALLBACK_ADMIN_MODERATE_PREFIX}reject_"

# Admin Slyot Actions (suffix will be response_id)
CB_ADMIN_MARK_SLYOT = f"{CALLBACK_ADMIN_SLYOT_PREFIX}mark_"
CB_ADMIN_CANCEL_SLYOT = f"{CALLBACK_ADMIN_SLYOT_PREFIX}cancel_"

# Redis Keys
REDIS_SLYOT_CANCEL_KEY_PREFIX = "slyot_cancel:" # Suffix will be response_id