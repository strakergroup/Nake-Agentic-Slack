"""Tests for Document MT error → Slack message mapping (RAY-81020)."""

from app.slack.document_mt_error_messages import slack_message_for_document_mt_error
from app.slack.templates.messages import (
    DocComplexityErrorMessage,
    DocInvalidPdfErrorMessage,
    DocMtMessage,
    DocParseErrorMessage,
    EvaluateErrorMessage,
    RequiresMtTokenMessage,
)


def _block_text(message) -> str:
    return message.blocks[0]["text"]["text"]


def test_sample_text_not_found_uses_producer_message():
    message = slack_message_for_document_mt_error(
        "sample_text_not_found",
        {"message": ":warning: No translatable text found.", "ext": ".docx"},
    )
    assert isinstance(message, DocParseErrorMessage)
    assert "No translatable text" in _block_text(message)


def test_sample_text_not_found_fallback_when_message_missing():
    message = slack_message_for_document_mt_error(
        "sample_text_not_found", {"ext": ".docx"}
    )
    assert isinstance(message, DocParseErrorMessage)
    assert "translatable text" in _block_text(message).lower()


def test_conversion_error_keeps_encrypted_copy():
    encrypted = (
        ":warning: This file is password-protected or encrypted, so we can't "
        "convert it for translation."
    )
    message = slack_message_for_document_mt_error(
        "conversion_error",
        {"message": encrypted, "ext": ".docx", "encrypted": True},
    )
    assert isinstance(message, DocParseErrorMessage)
    assert "password-protected" in _block_text(message)


def test_other_uses_generic_fallback():
    message = slack_message_for_document_mt_error(
        "other",
        {"message": "internal boom"},
        generic_fallback=DocMtMessage(),
    )
    assert isinstance(message, DocMtMessage)


def test_evaluate_generic_fallback():
    message = slack_message_for_document_mt_error(
        "other",
        {},
        generic_fallback=EvaluateErrorMessage(),
    )
    assert isinstance(message, EvaluateErrorMessage)


def test_invalid_pdf_and_complexity():
    assert isinstance(
        slack_message_for_document_mt_error("invalid_pdf", {"message": "bad pdf"}),
        DocInvalidPdfErrorMessage,
    )
    assert isinstance(
        slack_message_for_document_mt_error("file_complexity_error", {"ext": ".xlsx"}),
        DocComplexityErrorMessage,
    )


def test_insufficient_balance_requires_balance_message():
    balance = RequiresMtTokenMessage(1, 10)
    message = slack_message_for_document_mt_error(
        "insufficient_balance",
        {"balance": 1, "required": 10},
        balance_message=balance,
    )
    assert message is balance
