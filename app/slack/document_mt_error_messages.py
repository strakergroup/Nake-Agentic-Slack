"""Map Document MT ``error_type`` + ``error_data`` to Slack message templates.

RAY-81020: prefer producer ``error_data.message`` for known typed failures so
slack-ray does not grow a bespoke branch (and hardcoded copy) per incident.
"""

from __future__ import annotations

from typing import Any

from app.slack.templates.messages import (
    DocComplexityErrorMessage,
    DocInvalidPdfErrorMessage,
    DocMtMessage,
    DocParseErrorMessage,
    EvaluateErrorMessage,
    SlackMessage,
)

# Fallback when consumer emits sample_text_not_found without a user message.
_SAMPLE_TEXT_NOT_FOUND_FALLBACK = (
    ":warning: We couldn't find any translatable text in this file. It may be "
    "blank or image-only. Please add text content, or use OCR to extract text "
    "before uploading again."
)

# Types that already carry actionable ``error_data.message`` from the consumer.
_MESSAGE_DRIVEN_ERROR_TYPES = frozenset(
    {
        "conversion_error",
        "sample_text_not_found",
    }
)


def slack_message_for_document_mt_error(
    error_type: str | Any,
    error_data: dict[str, Any] | None,
    *,
    balance_message: SlackMessage | None = None,
    generic_fallback: SlackMessage | None = None,
) -> SlackMessage:
    """Build the ephemeral Slack message for a Document MT / evaluate error.

    ``balance_message`` must be supplied by the caller when
    ``error_type == insufficient_balance`` (admin vs non-admin is route-local).
    """
    payload = error_data or {}
    type_value = str(getattr(error_type, "value", error_type) or "other")

    if type_value == "insufficient_balance":
        if balance_message is None:
            raise ValueError("balance_message is required for insufficient_balance")
        return balance_message

    if type_value in _MESSAGE_DRIVEN_ERROR_TYPES:
        message = str(payload.get("message") or "").strip()
        if type_value == "sample_text_not_found" and not message:
            message = _SAMPLE_TEXT_NOT_FOUND_FALLBACK
        return DocParseErrorMessage(
            payload.get("ext", "") or "",
            payload.get("file_expected", "") or "",
            message,
        )

    if type_value == "file_complexity_error":
        return DocComplexityErrorMessage(payload.get("ext", "") or "")

    if type_value == "invalid_pdf":
        return DocInvalidPdfErrorMessage(payload.get("message", "") or "")

    return generic_fallback or DocMtMessage()


def document_mt_generic_fallback(*, evaluate: bool = False) -> SlackMessage:
    return EvaluateErrorMessage() if evaluate else DocMtMessage()
