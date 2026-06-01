"""Media-embedding spend routing tests (RAY-80000 §3.5).

Embedding subtitles into media is charged via the LanguageCloud API
(``/mt/embed``) so the gateway writes the self-describing usage row alongside the
debit. These assert the producer sends the duration, language count and a stable
per-task idempotency key, and that the key recipe keeps the embedding charge
distinct from the transcription charge for the same task.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth.connector import build_spend_idempotency_key, log_embedding_by_client_id


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
async def test_embedding_sends_duration_languages_and_key():
    """The /mt/embed payload carries duration, language count and idempotency."""
    cm, mock_http = _patch_async_client(
        {"transaction_uuid": "txn-embed", "replayed": False}
    )

    with (
        patch(
            "app.auth.connector.fetch_one", new=AsyncMock(return_value=_member_row())
        ),
        patch(
            "app.auth.connector.create_languagecloud_id_token", return_value="id-token"
        ),
        patch("app.auth.connector.httpx.AsyncClient", return_value=cm),
    ):
        transaction_uuid = await log_embedding_by_client_id(
            client_id="client-1",
            duration_ms=120_000,
            num_target_languages=3,
            file_name="clip.mp4",
            idempotency_key="key-embed",
        )

    assert transaction_uuid == "txn-embed"
    posted_json = mock_http.post.call_args.kwargs["json"]
    assert posted_json["duration_ms"] == 120_000
    assert posted_json["num_target_languages"] == 3
    assert posted_json["file_name"] == "clip.mp4"
    assert posted_json["idempotency_key"] == "key-embed"
    assert posted_json["app_name"] == "slack"


@pytest.mark.asyncio
async def test_embedding_omits_empty_optionals():
    """Without a file name or key the payload stays minimal (back-compat)."""
    cm, mock_http = _patch_async_client({"transaction_uuid": "txn-2"})

    with (
        patch(
            "app.auth.connector.fetch_one", new=AsyncMock(return_value=_member_row())
        ),
        patch(
            "app.auth.connector.create_languagecloud_id_token", return_value="id-token"
        ),
        patch("app.auth.connector.httpx.AsyncClient", return_value=cm),
    ):
        await log_embedding_by_client_id(
            client_id="client-1",
            duration_ms=1_000,
            num_target_languages=1,
        )

    posted_json = mock_http.post.call_args.kwargs["json"]
    assert "file_name" not in posted_json
    assert "idempotency_key" not in posted_json


def test_embedding_key_distinct_from_transcription():
    """Embedding and transcription for one task are charged as separate debits."""
    transcription = build_spend_idempotency_key(
        "slack", "task-1", "transcription", unit_type="milliseconds"
    )
    embedding = build_spend_idempotency_key(
        "slack", "task-1", "media_embedding", unit_type="milliseconds"
    )
    assert transcription != embedding


@pytest.mark.asyncio
async def test_embedding_raises_for_unknown_client():
    """A missing member is surfaced rather than silently dropping the charge."""
    with patch("app.auth.connector.fetch_one", new=AsyncMock(return_value=None)):
        with pytest.raises(Exception, match="not found"):
            await log_embedding_by_client_id(
                client_id="missing",
                duration_ms=1_000,
                num_target_languages=1,
            )
