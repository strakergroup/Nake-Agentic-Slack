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
    HeaderBlock,
    MarkdownTextObject,
    PlainTextObject,
    SectionBlock,
)
from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)

# Slack's limit for mrkdwn text in section blocks is 3000 characters
# Use a smaller limit to account for markdown formatting overhead
MAX_BLOCK_TEXT_LENGTH = 2800


def _truncate_text(text: str, max_length: int = MAX_BLOCK_TEXT_LENGTH) -> str:
    """Truncate text to fit within Slack's character limit.

    Args:
        text: Text to truncate
        max_length: Maximum length (default: 2800 to leave buffer for formatting)

    Returns:
        Truncated text with ellipsis if needed
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - 20] + "\n... (truncated)"


def _split_text_into_blocks(
    text: str, max_length: int = MAX_BLOCK_TEXT_LENGTH
) -> list[str]:
    """Split long text into chunks that fit within Slack's block limit.

    Args:
        text: Text to split
        max_length: Maximum length per chunk

    Returns:
        List of text chunks
    """
    if len(text) <= max_length:
        return [text]

    # Split by lines to avoid breaking in the middle of a line
    lines = text.split("\n")
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_length: int = 0

    for line in lines:
        line_length = len(line) + 1  # +1 for newline

        # If adding this line would exceed the limit, start a new chunk
        if current_length + line_length > max_length and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_length = line_length
        else:
            current_chunk.append(line)
            current_length += line_length

    # Add the last chunk
    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


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
    from app.config import Environment, config

    try:
        token = config.slack_dev_alert_bot_token.get_secret_value()
        # Select channel based on environment
        if config.environment == Environment.production:
            channel_id = config.slack_dev_alert_channel_id_production
        else:
            channel_id = config.slack_dev_alert_channel_id_non_production

        if not channel_id or not token:
            logger.debug(
                f"Slack notification skipped: channel_id={bool(channel_id)}, token={bool(token)}, "
                f"environment={config.environment.value}"
            )
            return

        # Build message
        env_prefix = (
            f"{config.environment.title()}"
            if config.environment != Environment.production
            else ""
        )

        # Format exception information if present
        if exc is not None:
            error_type = type(exc).__name__
            error_message = str(exc)
            error_traceback = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )

            # Truncate error message if too long
            error_message = _truncate_text(error_message)

            # Fallback text for notifications (also truncate)
            fallback_text = f"🚨 Exception: {error_type} - {error_message}"
            if msg:
                fallback_text = f"🚨 {msg}: {error_type} - {error_message}"
            fallback_text = _truncate_text(fallback_text, max_length=2000)
        else:
            # No exception, just a message
            error_type = None
            error_message = msg or "No message provided"
            error_message = _truncate_text(error_message)
            error_traceback = None
            fallback_text = f"🚨 Alert: {error_message}"
            fallback_text = _truncate_text(fallback_text, max_length=2000)

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
        # Split traceback into multiple blocks if needed
        if error_traceback:
            # Account for markdown formatting overhead: "*Traceback:*\n```\n" and "\n```"
            traceback_prefix = "*Traceback:*\n```\n"
            traceback_suffix = "\n```"
            available_length = (
                MAX_BLOCK_TEXT_LENGTH - len(traceback_prefix) - len(traceback_suffix)
            )

            traceback_chunks = _split_text_into_blocks(
                error_traceback, max_length=available_length
            )

            for i, chunk in enumerate(traceback_chunks):
                if i == 0:
                    # First chunk with header
                    blocks.append(
                        SectionBlock(
                            text=MarkdownTextObject(
                                text=f"{traceback_prefix}{chunk}{traceback_suffix}"
                            )
                        )
                    )
                else:
                    # Subsequent chunks without header
                    blocks.append(
                        SectionBlock(text=MarkdownTextObject(text=f"```\n{chunk}\n```"))
                    )

        # Add extra info if present
        if extra:
            extra_text = "\n".join([f"*{k}:* {v}" for k, v in extra.items()])
            # Truncate extra text to fit in a single block
            extra_text = _truncate_text(extra_text)
            blocks.append(
                SectionBlock(
                    text=MarkdownTextObject(text=f"*Extra Info:*\n{extra_text}")
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
    # Import config here to avoid circular imports
    from app.config import Environment, config

    # Skip Slack notifications when running locally
    if config.environment == Environment.local:
        logger.debug("Slack notification skipped: running in local environment")
        return

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

    # Also send to Slack (async, fire-and-forget) - only for ERROR, FATAL, or WARNING
    if severity.upper() in ("ERROR", "FATAL", "WARNING") and (
        exc is not None or msg is not None
    ):
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

    # Also send to Slack (async, fire-and-forget) - only for ERROR, FATAL, or WARNING
    if severity.upper() in ("ERROR", "FATAL", "WARNING") and msg:
        _schedule_slack_notification(None, msg, extra, severity)

    return result
