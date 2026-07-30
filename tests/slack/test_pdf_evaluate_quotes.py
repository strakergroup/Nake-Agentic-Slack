"""Tests for extract-priced HT/evaluate PDF quote sessions."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.slack.pdf_evaluate_quotes import (
    STAGE_AWAITING_ACCEPT,
    apply_pdf_evaluate_quote_result,
    build_pdf_evaluate_language_costs,
    post_pdf_evaluate_quote_message,
)


def test_build_pdf_evaluate_language_costs_maps_gridfs_and_language_codes():
    language_costs, updated_files, pdf_page_count = build_pdf_evaluate_language_costs(
        session_files=[
            {
                "id": "F1",
                "title": "1Test.pdf",
                "gridfs_file_id": "grid-1",
                "pdf_page_count": 0,
            }
        ],
        quote_files=[
            {
                "file_id": "grid-1",
                "file_name": "1Test.pdf",
                "character_count": 15,
                "pdf_conversion_page_count": 1,
                "pdf_conversion_tokens": 25,
                "target_languages": [
                    {"target_language": "fr", "tokens": 1, "cost_usd": 0.02}
                ],
            }
        ],
        language_uuid_by_code={"fr": "lang-fr"},
        language_names={"lang-fr": "French (France)"},
    )

    assert pdf_page_count == 1
    assert updated_files[0]["character_count"] == 15
    assert updated_files[0]["pdf_page_count"] == 1
    assert language_costs == [
        {
            "file_uuid": "F1",
            "file_label": "1Test.pdf",
            "value": "lang-fr",
            "label": "French (France)",
            "token": 1,
        }
    ]


@pytest.mark.asyncio
async def test_apply_pdf_evaluate_quote_result_stores_extract_totals():
    session = {
        "quote_id": "quote-1",
        "files": [
            {
                "id": "F1",
                "title": "1Test.pdf",
                "gridfs_file_id": "grid-1",
                "pdf_page_count": 0,
            }
        ],
        "language_uuid_by_code": {"fr": "lang-fr"},
        "language_names": {"lang-fr": "French (France)"},
        "stage": "quote_pending",
    }
    quote_data = {
        "quote_id": "quote-1",
        "total_tokens": 26,
        "pdf_conversion_tokens": 25,
        "preflight_task_uuid": "preflight-1",
        "files": [
            {
                "file_id": "grid-1",
                "file_name": "1Test.pdf",
                "character_count": 15,
                "pdf_conversion_page_count": 1,
                "pdf_conversion_tokens": 25,
                "target_languages": [
                    {"target_language": "fr", "tokens": 1, "cost_usd": 0.02}
                ],
            }
        ],
    }

    with (
        patch(
            "app.slack.pdf_evaluate_quotes.get_pdf_evaluate_quote_session",
            new=AsyncMock(
                side_effect=[session, {**session, "stage": STAGE_AWAITING_ACCEPT}]
            ),
        ),
        patch(
            "app.slack.pdf_evaluate_quotes.update_pdf_evaluate_quote_session",
            new=AsyncMock(),
        ) as mock_update,
    ):
        result = await apply_pdf_evaluate_quote_result("quote-1", quote_data)

    assert result is not None
    mock_update.assert_awaited_once()
    kwargs = mock_update.await_args.kwargs
    assert kwargs["ai_token_estimate"] == 1
    assert kwargs["pdf_page_count"] == 1
    assert kwargs["stage"] == STAGE_AWAITING_ACCEPT
    assert kwargs["language_costs"][0]["token"] == 1


@pytest.mark.asyncio
async def test_pdf_quote_callback_updates_existing_message_on_retry():
    client = AsyncMock()
    session = {
        "quote_id": "quote-1",
        "message_ts": "111.222",
        "ai_token_estimate": 1,
        "language_costs": [],
    }

    message_ts = await post_pdf_evaluate_quote_message(
        client,
        channel_id="C1",
        session=session,
    )

    assert message_ts == "111.222"
    client.chat_update.assert_awaited_once()
    assert client.chat_update.await_args.kwargs["ts"] == "111.222"
    client.chat_postMessage.assert_not_awaited()
