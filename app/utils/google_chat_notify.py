"""Google Chat incoming webhook notifications (standalone from the error-reporting pipeline).

Sending uses :func:`post_google_chat_notification`, which **returns immediately** and runs the
HTTP POST on the running event loop (``create_task``) or on a short-lived daemon thread when
no loop exists—callers are not blocked on network I/O.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from enum import StrEnum
from typing import Any

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "GoogleChatContext",
    "build_google_chat_text",
    "post_google_chat_notification",
]


def _runtime_env_and_config():
    """Lazy config access so tests can patch without importing ``app.config`` at collection."""
    from app.config import Environment, config

    return Environment, config


# Google Chat incoming webhook text payloads are limited; stay under API limits.
MAX_ALERT_TEXT_LENGTH = 3800

# U+1F6A8 POLICE CAR LIGHT — renders in Google Chat `text` webhooks.
CHAT_ALERT_PREFIX = "\U0001f6a8 "
# U+2139 INFORMATION SOURCE — calmer line prefix for INFO-only (no exception) messages.
CHAT_INFO_PREFIX = "\U00002139 "


class GoogleChatContext(StrEnum):
    """Call-site origin (mirrored report vs app-initiated); Chat text stays neutral."""

    BUGLOG = "buglog"
    APP = "app"


def _truncate_text(text: str, max_length: int = MAX_ALERT_TEXT_LENGTH) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 20] + "\n... (truncated)"


def _exception_heading() -> str:
    return "Exception"


def _alert_heading() -> str:
    return "Alert"


def _severity_normalized(severity: str) -> str:
    return (severity or "").strip().upper()


def _line_prefix_message_only(severity: str) -> str:
    if _severity_normalized(severity) == "INFO":
        return CHAT_INFO_PREFIX
    return CHAT_ALERT_PREFIX


def _notice_or_alert_heading(severity: str) -> str:
    if _severity_normalized(severity) == "INFO":
        return "Notice"
    return _alert_heading()


def build_google_chat_text(
    exc: BaseException | None,
    msg: str | None,
    extra: dict[str, Any] | None,
    severity: str,
    env_display: str,
    context: GoogleChatContext = GoogleChatContext.APP,
) -> str:
    """Compose plain-text body for a Google Chat webhook message."""
    _ = context  # reserved; same signature as callers passing BUGLOG vs APP

    lines: list[str] = []

    if exc is not None:
        lines.append(f"{CHAT_ALERT_PREFIX}{_exception_heading()}")
        lines.append("")
        lines.append(f"Environment: {env_display}")
        lines.append(f"Error Type: {exc.__class__.__name__}")
        resolved_msg = msg or f"{exc.__class__.__name__}: {str(exc)}"
        lines.append(f"Message: {_truncate_text(resolved_msg, max_length=2000)}")
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        lines.append("Traceback:")
        lines.append(_truncate_text(tb, max_length=2800))
    else:
        lines.append(
            f"{_line_prefix_message_only(severity)}{_notice_or_alert_heading(severity)}"
        )
        lines.append("")
        lines.append(f"Environment: {env_display}")
        lines.append(f"Severity: {severity}")
        alert_msg = msg or "No message provided"
        lines.append(f"Message: {_truncate_text(alert_msg, max_length=2000)}")

    if extra:
        lines.append("")
        lines.append("Extra:")
        for k, v in extra.items():
            lines.append(f"  {k}: {repr(v)}")

    body = "\n".join(lines)
    return _truncate_text(body)


async def _send_google_chat_notification(
    exc: BaseException | None = None,
    msg: str | None = None,
    extra: dict[str, Any] | None = None,
    severity: str = "ERROR",
    *,
    context: GoogleChatContext = GoogleChatContext.APP,
) -> None:
    """Perform the webhook POST; call only via ``post_google_chat_notification`` (or tests)."""
    Environment, config = _runtime_env_and_config()

    try:
        url = config.google_chat_webhook.get_secret_value().strip()
        if not url:
            logger.debug(
                "Google Chat notification skipped: GOOGLE_CHAT_WEBHOOK not configured "
                f"(environment={config.environment.value})"
            )
            return

        env_display = (
            config.environment.title()
            if config.environment != Environment.production
            else "Production"
        )

        text = build_google_chat_text(
            exc, msg, extra, severity, env_display, context=context
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json={"text": text})

        if response.status_code >= 400:
            logger.error(
                "Google Chat notification failed: HTTP %s (body length=%s)",
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
        # Log transport failure without alert payload, str(chat_error), or traceback:
        # httpx errors may embed the webhook URL in their string/repr.
        exc_type = type(exc).__name__ if exc is not None else None
        msg_len = len(msg) if msg else 0
        transport_type = type(chat_error).__name__
        logger.error(
            "Failed to send %s to Google Chat: transport_error_type=%s "
            "(payload exc_type=%s, has_msg=%s, msg_len=%s)",
            "exception" if exc else "alert",
            transport_type,
            exc_type,
            msg is not None and bool(str(msg).strip()),
            msg_len,
            exc_info=False,
        )


def post_google_chat_notification(
    exc: BaseException | None = None,
    msg: str | None = None,
    extra: dict[str, Any] | None = None,
    severity: str = "ERROR",
    *,
    context: GoogleChatContext = GoogleChatContext.APP,
) -> None:
    """Queue a Google Chat webhook POST without blocking the caller.

    Returns as soon as the work is handed off (async task or background thread). Does
    not wait for HTTP completion. When webhook URL is empty, the send is skipped
    inside :func:`_send_google_chat_notification`.

    ``severity`` appears in the body (e.g. INFO, ERROR); INFO-only messages use a calmer
    heading/prefix than error-style alerts.

    Use ``context=GoogleChatContext.BUGLOG`` when mirroring from ``buglog_notifier``;
    ``GoogleChatContext.APP`` for direct operational messages.
    """
    try:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                _send_google_chat_notification(
                    exc, msg, extra, severity, context=context
                )
            )
            logger.debug(
                "Queued Google Chat notification: exc=%s, msg=%s, context=%s",
                exc is not None,
                bool(msg),
                context.value,
            )
        except RuntimeError:
            import threading

            def run_async() -> None:
                try:
                    asyncio.run(
                        _send_google_chat_notification(
                            exc, msg, extra, severity, context=context
                        )
                    )
                except Exception as e:
                    logger.error(
                        "Error in async thread for Google Chat notification: %s",
                        type(e).__name__,
                        exc_info=False,
                    )

            thread = threading.Thread(target=run_async, daemon=True)
            thread.start()
            logger.debug(
                "Started thread for Google Chat notification: exc=%s, msg=%s",
                exc is not None,
                bool(msg),
            )
    except Exception as e:
        logger.error(
            "Failed to queue Google Chat notification: %s",
            type(e).__name__,
            exc_info=False,
        )
