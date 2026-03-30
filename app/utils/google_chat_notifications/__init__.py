"""Google Chat webhook notifications (standalone).

Use :func:`post_google_chat_notification` to send without blocking the caller on HTTP.
"""

from .notify import (
    GoogleChatContext,
    build_google_chat_text,
    post_google_chat_notification,
)

__all__ = [
    "GoogleChatContext",
    "build_google_chat_text",
    "post_google_chat_notification",
]
