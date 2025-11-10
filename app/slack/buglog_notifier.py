"""Slack notification helpers for BugLog exceptions and messages.

This module provides functions that send notifications to both BugLogHQ and Slack.
"""

import asyncio
import logging
import traceback
from typing import Any

from buglog import notify_exception as buglog_notify_exception
from buglog import notify_message as buglog_notify_message
from slack_sdk.models.blocks import (
    ActionsBlock,
    ButtonElement,
    HeaderBlock,
    MarkdownTextObject,
    PlainTextObject,
    SectionBlock,
)
from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)


async def _send_to_slack(
    exc: BaseException | None = None,
    msg: str | None = None,
    extra: dict[str, Any] | None = None,
    severity: str = "ERROR",
) -> None:
    """Send exception or message to Slack dev alert channel.

    Args:
        exc: Exception to report (optional)
        msg: Message to send (optional)
        extra: Extra information to include
        severity: Severity level (ERROR, INFO, FATAL)
    """
    # Import config here to avoid circular imports
    from app.config import Environment, config, domains

    try:
        token = config.slack_dev_alert_bot_token.get_secret_value()
        channel_id = config.slack_dev_alert_channel_id

        if not channel_id or not token:
            logger.debug(
                f"Slack notification skipped: channel_id={bool(channel_id)}, token={bool(token)}"
            )
            return

        # Build message
        env_prefix = (
            f"{config.environment.title()}"
            if config.environment != Environment.production
            else ""
        )

        # Create BugLogHQ link
        buglog_url = f"{domains.buglog}/bugLog/"

        # Format exception information if present
        if exc is not None:
            error_type = type(exc).__name__
            error_message = str(exc)
            error_traceback = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )

            # Truncate traceback if too long
            max_traceback_length = 3000
            if len(error_traceback) > max_traceback_length:
                error_traceback = (
                    error_traceback[:max_traceback_length] + "\n... (truncated)"
                )

            # Fallback text for notifications
            fallback_text = f"🚨 Exception: {error_type} - {error_message}"
            if msg:
                fallback_text = f"🚨 {msg}: {error_type} - {error_message}"
        else:
            # No exception, just a message
            error_type = None
            error_message = msg or "No message provided"
            error_traceback = None
            fallback_text = f"🚨 Alert: {error_message}"

        # Build Block Kit blocks
        blocks = [
            HeaderBlock(
                text=PlainTextObject(
                    text="🚨 Exception (from BugLogHQ)"
                    if exc
                    else "🚨 Alert (from BugLogHQ)",
                    emoji=True,
                )
            ),
            SectionBlock(
                fields=[
                    MarkdownTextObject(
                        text=f"*Environment:*\n{env_prefix or 'Production'}"
                    ),
                    MarkdownTextObject(
                        text=f"*{'Error Type' if exc else 'Severity'}:*\n`{error_type or severity}`"
                    ),
                ]
            ),
            SectionBlock(
                text=MarkdownTextObject(
                    text=f"*{'Error' if exc else 'Alert'} Message:*\n{error_message}"
                )
            ),
        ]

        # Add traceback block only if we have an exception
        if error_traceback:
            blocks.append(
                SectionBlock(
                    text=MarkdownTextObject(
                        text=f"*Traceback:*\n```\n{error_traceback}\n```"
                    )
                )
            )

        # Add extra info if present
        if extra:
            extra_text = "\n".join([f"*{k}:* {v}" for k, v in extra.items()])
            blocks.append(
                SectionBlock(
                    text=MarkdownTextObject(text=f"*Extra Info:*\n{extra_text}")
                )
            )

        # Add BugLogHQ link button
        blocks.append(
            ActionsBlock(
                elements=[
                    ButtonElement(
                        text=PlainTextObject(text="View in BugLogHQ", emoji=True),
                        url=buglog_url,
                        action_id="view_buglog",
                        style="primary",
                    )
                ]
            )
        )

        # Create Slack client and send message
        slack_client = AsyncWebClient(token=token)
        await slack_client.chat_postMessage(
            channel=channel_id,
            text=fallback_text,
            blocks=blocks,
        )

        logger.info(
            f"Successfully sent {'exception' if exc else 'alert'} notification to Slack channel {channel_id}"
        )

    except Exception as slack_error:
        # Don't let Slack notification failures break the app
        error_info = f"{type(exc).__name__}: {exc}" if exc else f"Message: {msg}"
        logger.error(
            f"Failed to send {'exception' if exc else 'alert'} to Slack: {slack_error}. "
            f"Original: {error_info}",
            exc_info=True,
        )


def _schedule_slack_notification(
    exc: BaseException | None = None,
    msg: str | None = None,
    extra: dict[str, Any] | None = None,
    severity: str = "ERROR",
) -> None:
    """Schedule a Slack notification task (async-safe).

    Args:
        exc: Exception to report (optional)
        msg: Message to send (optional)
        extra: Extra information to include
        severity: Severity level
    """
    try:
        # Try to get the current event loop
        try:
            loop = asyncio.get_running_loop()
            # If we're in an async context, schedule the task
            loop.create_task(_send_to_slack(exc, msg, extra, severity))
            logger.debug(
                f"Scheduled Slack notification task: exc={exc is not None}, msg={bool(msg)}"
            )
        except RuntimeError:
            # No running loop, create one for this task
            # Use a thread to avoid blocking
            import threading

            def run_async():
                try:
                    asyncio.run(_send_to_slack(exc, msg, extra, severity))
                except Exception as e:
                    logger.error(
                        f"Error in async thread for Slack notification: {e}",
                        exc_info=True,
                    )

            thread = threading.Thread(target=run_async, daemon=True)
            thread.start()
            logger.debug(
                f"Started thread for Slack notification: exc={exc is not None}, msg={bool(msg)}"
            )
    except Exception as e:
        # Don't let Slack failures break BugLogHQ notifications
        logger.error(f"Failed to schedule Slack notification: {e}", exc_info=True)


def notify_exception(
    exc: BaseException | None = None,
    msg: str | None = None,
    extra: dict[str, Any] | None = None,
    severity: str = "ERROR",
) -> bool:
    """Send exception to both BugLogHQ and Slack.

    This function wraps buglog.notify_exception and also sends a notification
    to the Slack dev alert channel.

    Args:
        exc: Exception to report. If not given, automatically retrieved with sys.exc_info.
        msg: Message to send. If not given, derived from the exception.
        extra: Extra information to add to the report.
        severity: One of INFO, ERROR, FATAL. Defaults to ERROR.

    Returns:
        bool: True if the exception was sent to BugLogHQ.
    """
    # Send to BugLogHQ first
    result = buglog_notify_exception(exc, msg, extra, severity)

    # Also send to Slack (async, fire-and-forget)
    if exc is not None or msg is not None:
        _schedule_slack_notification(exc, msg, extra, severity)

    return result


def notify_message(
    msg: str,
    extra: dict[str, Any] | None = None,
    severity: str = "INFO",
) -> bool:
    """Send message to both BugLogHQ and Slack.

    This function wraps buglog.notify_message and also sends a notification
    to the Slack dev alert channel.

    Args:
        msg: Message to send. If empty, does nothing.
        extra: Extra information to add to the report.
        severity: One of INFO, ERROR, FATAL. Defaults to INFO.

    Returns:
        bool: True if the message was sent to BugLogHQ.
    """
    # Send to BugLogHQ first
    result = buglog_notify_message(msg, extra, severity)

    # Also send to Slack (async, fire-and-forget)
    if msg:
        _schedule_slack_notification(None, msg, extra, severity)

    return result
