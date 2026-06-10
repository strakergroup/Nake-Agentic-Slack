"""Transcription spend routing tests (RAY-80000 §3.2).

Transcription is charged via the LanguageCloud API (``/mt/transcribe``) so the
gateway writes the self-describing usage row alongside the debit. These assert
the producer now sends the detected source language and a stable idempotency
key, and that the key recipe distinguishes genuinely separate charges.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth.connector import build_spend_idempotency_key, log_transcribe_by_client_id


class TestBuildSpendIdempotencyKey:
    def test_is_deterministic(self):
        """A redelivered transcription task replays to one debit."""
        a = build_spend_idempotency_key(
            "slack", "task-1", "transcription", unit_type="milliseconds"
        )
        b = build_spend_idempotency_key(
            "slack", "task-1", "transcription", unit_type="milliseconds"
        )
        assert a == b

    def test_distinct_tasks_differ(self):
        """Two separate transcription jobs are charged separately."""
        a = build_spend_idempotency_key(
            "slack", "task-1", "transcription", unit_type="milliseconds"
        )
        b = build_spend_idempotency_key(
            "slack", "task-2", "transcription", unit_type="milliseconds"
        )
        assert a != b

    def test_returns_sha256_hexdigest(self):
        key = build_spend_idempotency_key(
            "slack", "t", "transcription", unit_type="milliseconds"
        )
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)


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


@pytest.mark.asyncio
async def test_log_transcribe_sends_source_language_and_idempotency():
    """The /mt/transcribe payload carries source_language + idempotency_key."""
    cm, mock_http = _patch_async_client(
        {"transaction_uuid": "txn-1", "replayed": False}
    )

    with (
        patch(
            "app.auth.connector.fetch_one",
            new=AsyncMock(
                return_value={
                    "obj_uuid": "client-1",
                    "given_name": "Test",
                    "family_name": "User",
                    "email_primary": "test@example.com",
                    "active": 1,
                }
            ),
        ),
        patch(
            "app.auth.connector.create_languagecloud_id_token",
            return_value="id-token",
        ),
        patch("app.auth.connector.httpx.AsyncClient", return_value=cm),
    ):
        tokens, transaction_uuid = await log_transcribe_by_client_id(
            client_id="client-1",
            duration_ms=60_000,
            file_name="clip.mp4",
            source_language="ja",
            idempotency_key="key-abc",
        )

    assert tokens == 100  # ceil(60000 / 600)
    assert transaction_uuid == "txn-1"
    posted_json = mock_http.post.call_args.kwargs["json"]
    assert posted_json["source_language"] == "ja"
    assert posted_json["idempotency_key"] == "key-abc"
    assert posted_json["duration_ms"] == 60_000
    assert posted_json["file_name"] == "clip.mp4"


@pytest.mark.asyncio
async def test_log_transcribe_omits_empty_optionals():
    """Without a detected language or key the payload stays minimal (back-compat)."""
    cm, mock_http = _patch_async_client({"transaction_uuid": "txn-2"})

    with (
        patch(
            "app.auth.connector.fetch_one",
            new=AsyncMock(
                return_value={
                    "obj_uuid": "client-1",
                    "given_name": "",
                    "family_name": "",
                    "email_primary": "",
                    "active": 1,
                }
            ),
        ),
        patch(
            "app.auth.connector.create_languagecloud_id_token",
            return_value="id-token",
        ),
        patch("app.auth.connector.httpx.AsyncClient", return_value=cm),
    ):
        await log_transcribe_by_client_id(
            client_id="client-1",
            duration_ms=1_200,
            file_name="a.wav",
        )

    posted_json = mock_http.post.call_args.kwargs["json"]
    assert "source_language" not in posted_json
    assert "idempotency_key" not in posted_json
