"""Inline/channel MT spend routing tests (RAY-80000 §3.4).

Slack channel/shortcut MT is translated by the sup-mt-service pipeline (with
glossaries + multi-service routing), so it cannot use ``/mt/translate``. The
charge is recorded via the charge-only ``/mt/inline-usage`` endpoint so the
gateway writes the self-describing usage row alongside the debit. These assert
the producer sends the billed languages, engine, source language and a stable
per-message idempotency key.
"""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth.connector import (
    build_spend_idempotency_key,
    log_inline_mt_usage_by_client_id,
)


def _inline_key(message_ts: str, source_text: str, usage_type: str) -> str:
    """Mirror the producer's inline-MT key recipe (ray.py): the submission id is
    ``{message_ts}:{sha256(source_text)[:16]}`` so redeliveries dedupe but edits
    (new content) are charged."""
    fingerprint = hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:16]
    return build_spend_idempotency_key(
        app_source="slack",
        submission_id=f"{message_ts}:{fingerprint}",
        service=usage_type,
        unit_type="characters",
    )


class TestInlineIdempotencyBoundary:
    """False-positive boundary for inline MT (RAY-80000 §3.4)."""

    def test_redelivery_replays_to_one_debit(self):
        """Same message + same content (a stream redelivery) -> one charge."""
        a = _inline_key("1717.001", "hello world", "channel_translation")
        b = _inline_key("1717.001", "hello world", "channel_translation")
        assert a == b

    def test_edit_is_charged_separately(self):
        """Same message ts but edited content -> a distinct, billable key."""
        original = _inline_key("1717.001", "hello world", "channel_translation")
        edited = _inline_key("1717.001", "hello there", "channel_translation")
        assert original != edited

    def test_distinct_messages_differ(self):
        """Two different messages with identical text are charged separately."""
        a = _inline_key("1717.001", "same text", "channel_translation")
        b = _inline_key("1717.002", "same text", "channel_translation")
        assert a != b


def _patch_async_client(json_body):
    """Return a patch target for httpx.AsyncClient that yields a mocked client."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=json_body)

    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_response)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_http)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, mock_http


def _member_row():
    return {
        "obj_uuid": "client-1",
        "given_name": "Test",
        "family_name": "User",
        "email_primary": "test@example.com",
        "active": 1,
    }


@pytest.mark.asyncio
async def test_inline_usage_sends_full_payload():
    """The /mt/inline-usage payload carries languages, engine, source + key."""
    cm, mock_http = _patch_async_client(
        {"transaction_uuid": "txn-inline", "replayed": False}
    )

    with (
        patch(
            "app.auth.connector.fetch_one", new=AsyncMock(return_value=_member_row())
        ),
        patch(
            "app.auth.connector.create_languagecloud_id_token",
            return_value="id-token",
        ),
        patch("app.auth.connector.httpx.AsyncClient", return_value=cm),
    ):
        transaction_uuid = await log_inline_mt_usage_by_client_id(
            client_id="client-1",
            text_length=200,
            target_languages=["es", "fr"],
            usage_type="channel_translation",
            source_language="en",
            engine="google,microsoft",
            channel_name="general",
            word_count=35,
            idempotency_key="key-abc",
            email="poster@example.com",
            client_name="Channel Poster",
            group_uuid="billing-group-uuid",
        )

    assert transaction_uuid == "txn-inline"
    posted_json = mock_http.post.call_args.kwargs["json"]
    assert posted_json["text_length"] == 200
    assert posted_json["target_languages"] == ["es", "fr"]
    assert posted_json["usage_type"] == "channel_translation"
    assert posted_json["source_language"] == "en"
    assert posted_json["engine"] == "google,microsoft"
    assert posted_json["channel_name"] == "general"
    # word_count is a typed report column (RAY-80000).
    assert posted_json["word_count"] == 35
    assert posted_json["idempotency_key"] == "key-abc"
    assert posted_json["app_name"] == "slack"
    # Group-billed channel MT carries the poster identity for the usage report.
    assert posted_json["email"] == "poster@example.com"
    assert posted_json["client_name"] == "Channel Poster"
    # The resolved billing group is sent so the ledger group_uuid is not the org.
    assert posted_json["group_uuid"] == "billing-group-uuid"


@pytest.mark.asyncio
async def test_inline_usage_omits_empty_optionals():
    """Without engine/source/channel/key the payload stays minimal (back-compat)."""
    cm, mock_http = _patch_async_client({"transaction_uuid": "txn-2"})

    with (
        patch(
            "app.auth.connector.fetch_one", new=AsyncMock(return_value=_member_row())
        ),
        patch(
            "app.auth.connector.create_languagecloud_id_token",
            return_value="id-token",
        ),
        patch("app.auth.connector.httpx.AsyncClient", return_value=cm),
    ):
        await log_inline_mt_usage_by_client_id(
            client_id="client-1",
            text_length=50,
            target_languages=["ja"],
            usage_type="shortcut_translate",
        )

    posted_json = mock_http.post.call_args.kwargs["json"]
    assert posted_json["target_languages"] == ["ja"]
    assert "source_language" not in posted_json
    assert "engine" not in posted_json
    assert "channel_name" not in posted_json
    assert "word_count" not in posted_json
    assert "idempotency_key" not in posted_json
    assert "email" not in posted_json
    assert "client_name" not in posted_json
    assert "group_uuid" not in posted_json


@pytest.mark.asyncio
async def test_inline_usage_uses_group_token_when_no_member():
    """Channel auto-translate bills the org: when the client_id has no member row
    (poster never direct-logged-in), authenticate with a group token instead of
    raising, so the charge still lands (RAY-80000). The gateway's /mt/inline-usage
    accepts a user-or-group principal."""
    cm, mock_http = _patch_async_client({"transaction_uuid": "txn-org"})

    with (
        patch("app.auth.connector.fetch_one", new=AsyncMock(return_value=None)),
        patch(
            "app.auth.connector.create_languagecloud_group_token",
            return_value="group-token",
        ) as mock_group_token,
        patch(
            "app.auth.connector.create_languagecloud_id_token",
            return_value="id-token",
        ) as mock_id_token,
        patch("app.auth.connector.httpx.AsyncClient", return_value=cm),
    ):
        transaction_uuid = await log_inline_mt_usage_by_client_id(
            client_id="org-uuid",
            text_length=100,
            target_languages=["es"],
            usage_type="channel_translation",
            email="poster@example.com",
        )

    assert transaction_uuid == "txn-org"
    # No member row -> mint a group token (not a user id token) for the org.
    mock_id_token.assert_not_called()
    mock_group_token.assert_called_once()
    assert mock_group_token.call_args.kwargs["uuid"] == "org-uuid"
    assert mock_group_token.call_args.kwargs["aud"] == "languagecloud-api"
    # The request authenticates with the group token and still posts the charge.
    assert mock_http.post.call_args.kwargs["headers"]["Authorization"] == (
        "Bearer group-token"
    )
    posted_json = mock_http.post.call_args.kwargs["json"]
    assert posted_json["usage_type"] == "channel_translation"
    assert posted_json["email"] == "poster@example.com"
