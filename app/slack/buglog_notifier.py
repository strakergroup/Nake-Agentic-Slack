"""Google Chat webhook notification helpers for BugLog exceptions and messages.

This module provides functions that send notifications to both BugLogHQ and Google Chat.
"""

import asyncio
import logging
import traceback
from typing import Any

import httpx
from buglog import notify_exception as buglog_notify_exception
from buglog import notify_message as buglog_notify_message

logger = logging.getLogger(__name__)

# Google Chat incoming webhook text payloads are limited; stay under API limits.
MAX_ALERT_TEXT_LENGTH = 3800


def _truncate_text(text: str, max_length: int = MAX_ALERT_TEXT_LENGTH) -> str:
    """Truncate text to fit within Google Chat webhook limits.

    Args:
        text: Text to truncate
        max_length: Maximum length

    Returns:
        Truncated text with ellipsis if needed
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - 20] + "\n... (truncated)"


def _build_alert_text(
    exc: BaseException | None,
    msg: str | None,
    extra: dict[str, Any] | None,
    severity: str,
    env_display: str,
) -> str:
    """Build plain-text body for Google Chat webhook."""
    lines: list[str] = []
    if exc is not None:
        lines.append("Exception (from BugLogHQ)")
        lines.append(f"Environment: {env_display}")
        lines.append(f"Error type: {type(exc).__name__}")
        if msg:
            lines.append(f"Context: {msg}")
        err_msg = _truncate_text(str(exc), max_length=800)
        lines.append(f"Error message: {err_msg}")
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        lines.append("Traceback:")
        lines.append(tb)
    else:
        lines.append("Alert (from BugLogHQ)")
        lines.append(f"Environment: {env_display}")
        lines.append(f"Severity: {severity}")
        alert_msg = msg or "No message provided"
        lines.append(f"Message: {_truncate_text(alert_msg, max_length=2000)}")

    if extra:
        lines.append("Extra:")
        for k, v in extra.items():
            lines.append(f"  {k}: {v}")

    body = "\n".join(lines)
    return _truncate_text(body)


async def _send_to_google_chat(
    exc: BaseException | None = None,
    msg: str | None = None,
    extra: dict[str, Any] | None = None,
    severity: str = "ERROR",
) -> None:
    """Send exception or message to Google Chat via incoming webhook.

    Args:
        exc: Exception to report (optional)
        msg: Message to send (optional)
        extra: Extra information to include
        severity: Severity level (ERROR, INFO, FATAL)
    """
    from app.config import Environment, config

    try:
        url = config.google_chat_webhook_pm.get_secret_value().strip()
        if not url:
            logger.debug(
                "Google Chat dev alert skipped: GOOGLE_CHAT_WEBHOOK_PM not configured "
                f"(environment={config.environment.value})"
            )
            return

        env_display = (
            config.environment.title()
            if config.environment != Environment.production
            else "Production"
        )

        text = _build_alert_text(exc, msg, extra, severity, env_display)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json={"text": text})

        if response.status_code >= 400:
            logger.error(
                "Google Chat dev alert failed: HTTP %s (body length=%s)",
                response.status_code,
                len(response.text),
                exc_info=False,
            )
            return

        logger.info(
            "Successfully sent %s notification to Google Chat webhook",
            "exception" if exc else "alert",
        )

    except Exception as chat_error:
        error_info = f"{type(exc).__name__}: {exc}" if exc else f"Message: {msg}"
        logger.error(
            "Failed to send %s to Google Chat: %s. Original: %s",
            "exception" if exc else "alert",
            chat_error,
            error_info,
            exc_info=True,
        )


def _schedule_dev_alert_notification(
    exc: BaseException | None = None,
    msg: str | None = None,
    extra: dict[str, Any] | None = None,
    severity: str = "ERROR",
) -> None:
    """Schedule a Google Chat dev alert task (async-safe).

    Args:
        exc: Exception to report (optional)
        msg: Message to send (optional)
        extra: Extra information to include
        severity: Severity level
    """
    from app.config import Environment, config

    if config.environment == Environment.local:
        logger.debug("Google Chat dev alert skipped: running in local environment")
        return

    try:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_send_to_google_chat(exc, msg, extra, severity))
            logger.debug(
                "Scheduled Google Chat dev alert: exc=%s, msg=%s",
                exc is not None,
                bool(msg),
            )
        except RuntimeError:
            import threading

            def run_async() -> None:
                try:
                    asyncio.run(_send_to_google_chat(exc, msg, extra, severity))
                except Exception as e:
                    logger.error(
                        "Error in async thread for Google Chat dev alert: %s",
                        e,
                        exc_info=True,
                    )

            thread = threading.Thread(target=run_async, daemon=True)
            thread.start()
            logger.debug(
                "Started thread for Google Chat dev alert: exc=%s, msg=%s",
                exc is not None,
                bool(msg),
            )
    except Exception as e:
        logger.error("Failed to schedule Google Chat dev alert: %s", e, exc_info=True)


def notify_exception(
    exc: BaseException | None = None,
    msg: str | None = None,
    extra: dict[str, Any] | None = None,
    severity: str = "ERROR",
) -> bool:
    """Send exception to both BugLogHQ and Google Chat.

    This function wraps buglog.notify_exception and also sends a notification
    to the Google Chat dev alert webhook.

    Args:
        exc: Exception to report. If not given, automatically retrieved with sys.exc_info.
        msg: Message to send. If not given, derived from the exception.
        extra: Extra information to add to the report.
        severity: One of INFO, ERROR, FATAL. Defaults to ERROR.

    Returns:
        bool: True if the exception was sent to BugLogHQ.
    """
    result = buglog_notify_exception(exc, msg, extra, severity)

    if severity.upper() in ("ERROR", "FATAL", "WARNING") and (
        exc is not None or msg is not None
    ):
        _schedule_dev_alert_notification(exc, msg, extra, severity)

    return result


def notify_message(
    msg: str,
    extra: dict[str, Any] | None = None,
    severity: str = "INFO",
) -> bool:
    """Send message to both BugLogHQ and Google Chat.

    This function wraps buglog.notify_message and also sends a notification
    to the Google Chat dev alert webhook for elevated severities.

    Args:
        msg: Message to send. If empty, does nothing.
        extra: Extra information to add to the report.
        severity: One of INFO, ERROR, FATAL. Defaults to INFO.

    Returns:
        bool: True if the message was sent to BugLogHQ.
    """
    result = buglog_notify_message(msg, extra, severity)

    if severity.upper() in ("ERROR", "FATAL", "WARNING") and msg:
        _schedule_dev_alert_notification(None, msg, extra, severity)

    return result
