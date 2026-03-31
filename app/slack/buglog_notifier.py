"""BugLog notification wrappers with Google Chat mirroring.

BugLog payloads are sent via the ``buglog`` package unchanged. When there is an
exception and/or a message with non-whitespace content to mirror, the same payload is **also**
queued for Google Chat via ``app.utils.google_chat_notify``, regardless of
whether BugLogHQ accepted the report (``True``/``False``). That keeps Chat as a
separate channel when BugLog is down or misconfigured. Delivery is skipped when
the webhook URL is unset — see ``post_google_chat_notification``.
"""

from typing import Any

from buglog import notify_exception as buglog_notify_exception
from buglog import notify_message as buglog_notify_message


def _should_mirror_to_chat(exc: BaseException | None, msg: str | None) -> bool:
    """True if Google Chat should see a mirror (exception present or non-blank message)."""
    if exc is not None:
        return True
    if msg is None:
        return False
    return bool(str(msg).strip())


def _schedule_chat_mirror(
    exc: BaseException | None,
    msg: str | None,
    extra: dict[str, Any] | None,
    severity: str,
) -> None:
    from app.utils.google_chat_notify import (
        GoogleChatContext,
        post_google_chat_notification,
    )

    post_google_chat_notification(
        exc, msg, extra, severity, context=GoogleChatContext.BUGLOG
    )


def notify_exception(
    exc: BaseException | None = None,
    msg: str | None = None,
    extra: dict[str, Any] | None = None,
    severity: str = "ERROR",
) -> bool:
    """Send exception to BugLogHQ and mirror to Google Chat when applicable.

    Args:
        exc: Exception to report. If not given, automatically retrieved with sys.exc_info.
        msg: Message to send. If not given, derived from the exception.
        extra: Extra information to add to the report.
        severity: One of INFO, ERROR, FATAL. Defaults to ERROR.

    Returns:
        bool: True if BugLogHQ accepted the report only. Google Chat mirroring
        (when ``exc`` is set or ``msg`` has non-whitespace text) is scheduled
        independently of this value.
    """
    result = buglog_notify_exception(exc, msg, extra, severity)

    if _should_mirror_to_chat(exc, msg):
        _schedule_chat_mirror(exc, msg, extra, severity)

    return result


def notify_message(
    msg: str,
    extra: dict[str, Any] | None = None,
    severity: str = "INFO",
) -> bool:
    """Send message to BugLogHQ and mirror to Google Chat when ``msg`` has content.

    Args:
        msg: Message to send. BugLog is always invoked; Chat mirroring only if
            ``msg`` contains non-whitespace characters.
        extra: Extra information to add to the report.
        severity: One of INFO, ERROR, FATAL. Defaults to INFO.

    Returns:
        bool: True if BugLogHQ accepted the report only. Google Chat mirroring
        for ``msg`` with non-whitespace content is scheduled independently of this value.
    """
    result = buglog_notify_message(msg, extra, severity)

    if msg and str(msg).strip():
        _schedule_chat_mirror(None, msg, extra, severity)

    return result
