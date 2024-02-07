from buglog import notify_exception

from ..redis import redis_conn


async def auto_translate_permissions_reminder(client_id: str, channel_id: str) -> bool:
    """Checks if the reminder for auto-translate permissions has been sent recently.
    If not, this function will return `True` and will return False for the next hour.
    This is to prevent spamming the user with the same reminder.

    Args:
        client_id (str): The LC ID of the user (`sitemanager.obj_m_member.obj_uuid`).
        channel_id (str): The Slack channel ID.

    Returns:
        bool: The reminder has not been sent recently.
    """
    key = f"slack-ray-translator:timer:auto-translate-permissions:{client_id}:{channel_id}"
    try:
        is_reminder_sent = await redis_conn.exists(key)
        if is_reminder_sent:
            return False
    except Exception as e:
        notify_exception(e)

    # Logs the reminder for 1 hour.
    try:
        await redis_conn.set(key, 1, ex=3600)
    except Exception as e:
        notify_exception(e)
    return True
