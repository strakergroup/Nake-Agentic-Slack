"""Unit tests for the durable SAQ task functions (RAY-79638).

Tasks are tested without a running SAQ worker by invoking them directly with
a stub context. External dependencies (Slack SDK, file server, slack_user
lookup, slack_job updates) are mocked so the tests run hermetically.
"""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.ray.submissions import SubmissionStatus
from app.saq_jobs.tasks import (
    charge_document_mt,
    charge_inline_mt_usage,
    persist_log_notification,
    process_document_mt_quote_preflight,
    process_document_mt_submission,
    process_evaluation_submission,
    slack_upload_mt_result,
    slack_upload_transcription,
    slack_upload_verify_complete,
)


def _ctx(attempts: int = 1, retryable: bool = True) -> dict[str, Any]:
    """Build a stub SAQ context object."""
    job = MagicMock()
    job.attempts = attempts
    job.retryable = retryable
    return {"job": job}


@pytest.fixture
def slack_user():
    user = MagicMock()
    user.bot_token = "xoxb-fake-test-token"  # noqa: S105 — test fixture
    user.ray_client_id = str(uuid4())
    return user


@pytest.fixture(autouse=True)
def _quiet_notify():
    """Avoid touching BugLog/Google Chat from inside the task body."""
    with patch("app.saq_jobs.tasks.notify_exception"):
        yield


# --------------------------------------------------------------------------- #
# process_document_mt_submission / process_evaluation_submission
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_process_document_mt_submission_downloads_uploads_and_submits():
    ray_client = MagicMock()
    ray_client.is_trial = False
    ray_client.id_token = "id-token"
    super_group = MagicMock()
    super_group.verify_organization_uuid = "org-uuid"
    ray_connection = MagicMock()
    ray_connection.client = ray_client
    ray_connection.super_group = [super_group]
    record = MagicMock(id=123)
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()

    with (
        patch(
            "app.auth.connector.get_ray_connection",
            new=AsyncMock(return_value=ray_connection),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.pptx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.ray.utils.upload_to_file_server", new=AsyncMock(return_value="grid-1")
        ),
        patch(
            "app.ray.submissions.check_and_record_submission_async",
            new=AsyncMock(return_value=(False, record)),
        ),
        patch(
            "app.slack.listener_actions.document_machine_translate", new=AsyncMock()
        ) as mock_mt,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_document_mt_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pptx"}],
            source_language="en",
            target_languages=["zh-CN"],
        )

    assert result["status"] == "processed"
    mock_mt.assert_awaited_once()
    assert mock_mt.await_args.args[1:] == ("grid-1", "en", ["zh-CN"], {"zh-CN": 123})
    fake_slack.chat_postMessage.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_document_mt_quote_preflight_uploads_and_requests_quote():
    ray_client = MagicMock()
    ray_client.is_trial = False
    ray_client.id_token = "id-token"
    ray_client.id = "client-1"
    ray_client.user_group_id = "group-1"
    super_group = MagicMock()
    super_group.id = "group-1"
    super_group.verify_organization_uuid = "org-uuid"
    ray_connection = MagicMock()
    ray_connection.client = ray_client
    ray_connection.super_group = [super_group]
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()

    with (
        patch(
            "app.auth.connector.get_ray_connection",
            new=AsyncMock(return_value=ray_connection),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch(
            "app.auth.connector.get_group_mt_engine",
            new=AsyncMock(return_value="google"),
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.pptx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.ray.utils.upload_to_file_server", new=AsyncMock(return_value="grid-1")
        ),
        patch(
            "app.ray.submissions._hash_file_content_sha256_hex",
            return_value="hash-1",
        ),
        patch("os.path.getsize", return_value=1234),
        patch(
            "app.slack.document_mt_quotes.save_document_mt_quote_session",
            new=AsyncMock(),
        ) as mock_save,
        patch(
            "app.api.stream_proxy.send_document_mt_quote_request",
            new=AsyncMock(),
        ) as mock_quote,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_document_mt_quote_preflight(
            _ctx(),
            quote_id="quote-1",
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pptx", "size": 1234}],
            source_language="en",
            target_languages=["zh-CN"],
        )

    assert result == {"status": "quote_requested", "file_count": 1}
    mock_save.assert_awaited_once()
    saved_session = mock_save.await_args.args[0]
    assert saved_session["quote_id"] == "quote-1"
    assert saved_session["files"][0]["file_id"] == "grid-1"
    assert saved_session["files"][0]["file_hash"] == "hash-1"
    mock_quote.assert_awaited_once()
    assert mock_quote.await_args.kwargs["quote_id"] == "quote-1"
    assert mock_quote.await_args.kwargs["files"][0]["file_id"] == "grid-1"
    assert mock_quote.await_args.kwargs["client_id"] == "org-uuid"


@pytest.mark.asyncio
async def test_process_document_mt_quote_preflight_org_billed_without_member():
    """Org-billed Document MT quote proceeds when the poster has no LC member link.

    Regression: member-only ``no_ray_client`` left Slack stuck on
    "Preparing an AI Translate quote..." for org-billed workspaces.
    """
    super_group = MagicMock()
    super_group.id = "group-1"
    super_group.verify_organization_uuid = "org-uuid"
    ray_connection = MagicMock()
    ray_connection.client = None
    ray_connection.super_group = [super_group]
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()

    with (
        patch(
            "app.auth.connector.get_ray_connection",
            new=AsyncMock(return_value=ray_connection),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch(
            "app.auth.connector.get_group_mt_engine",
            new=AsyncMock(return_value="google"),
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.pptx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.ray.utils.upload_to_file_server", new=AsyncMock(return_value="grid-1")
        ),
        patch(
            "app.ray.submissions._hash_file_content_sha256_hex",
            return_value="hash-1",
        ),
        patch("os.path.getsize", return_value=1234),
        patch(
            "app.slack.document_mt_quotes.save_document_mt_quote_session",
            new=AsyncMock(),
        ),
        patch(
            "app.api.stream_proxy.send_document_mt_quote_request",
            new=AsyncMock(),
        ) as mock_quote,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_document_mt_quote_preflight(
            _ctx(),
            quote_id="quote-1",
            user_id="U1",
            team_id="T1",
            enterprise_id="E27SFGS2W",
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pptx", "size": 1234}],
            source_language="en",
            target_languages=["zh-CN"],
        )

    assert result == {"status": "quote_requested", "file_count": 1}
    mock_quote.assert_awaited_once()
    # client_id is the org uuid here, so the poster must ride along separately
    # or the echoed quote response cannot reach a real Slack user.
    assert mock_quote.await_args.kwargs["client_id"] == "org-uuid"
    assert mock_quote.await_args.kwargs["team_id"] == "T1"
    assert mock_quote.await_args.kwargs["slack_user_id"] == "U1"
    assert mock_quote.await_args.kwargs["enterprise_id"] == "E27SFGS2W"


@pytest.mark.asyncio
async def test_process_document_mt_quote_preflight_requires_super_group():
    """Unlinked workspaces must not request a Document MT quote."""
    ray_connection = MagicMock()
    ray_connection.client = None
    ray_connection.super_group = []

    with patch(
        "app.auth.connector.get_ray_connection",
        new=AsyncMock(return_value=ray_connection),
    ):
        result = await process_document_mt_quote_preflight(
            _ctx(),
            quote_id="quote-1",
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pptx", "size": 1234}],
            source_language="en",
            target_languages=["zh-CN"],
        )

    assert result == {"status": "no_super_group"}


@pytest.mark.asyncio
async def test_process_document_mt_submission_org_billed_without_member():
    """Org-billed Document MT proceeds when the poster has no LC member link."""
    super_group = MagicMock()
    super_group.verify_organization_uuid = "org-uuid"
    ray_connection = MagicMock()
    ray_connection.client = None
    ray_connection.super_group = [super_group]
    record = MagicMock(id=456)
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()

    with (
        patch(
            "app.auth.connector.get_ray_connection",
            new=AsyncMock(return_value=ray_connection),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.pptx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.ray.utils.upload_to_file_server", new=AsyncMock(return_value="grid-2")
        ),
        patch(
            "app.ray.submissions.check_and_record_submission_async",
            new=AsyncMock(return_value=(False, record)),
        ),
        patch(
            "app.slack.listener_actions.document_machine_translate", new=AsyncMock()
        ) as mock_mt,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_document_mt_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id="E27SFGS2W",
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pptx"}],
            source_language="en",
            target_languages=["fr"],
        )

    assert result["status"] == "processed"
    mock_mt.assert_awaited_once()
    assert mock_mt.await_args.args[0]["enterprise_id"] == "E27SFGS2W"


@pytest.mark.asyncio
async def test_process_document_mt_submission_uses_cached_quote_file_state():
    ray_client = MagicMock()
    ray_client.is_trial = False
    ray_client.id_token = "id-token"
    super_group = MagicMock()
    super_group.verify_organization_uuid = "org-uuid"
    ray_connection = MagicMock()
    ray_connection.client = ray_client
    ray_connection.super_group = [super_group]
    record = MagicMock(id=123)
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()
    session = {
        "quote_id": "quote-1",
        "user_id": "U1",
        "team_id": "T1",
        "channel_id": "C1",
        "source_language": "en",
        "target_languages": ["zh-CN"],
        "preflight_task_uuid": "preflight-1",
        "files": [
            {
                "slack_file_id": "F1",
                "title": "a.pptx",
                "file_id": "grid-1",
                "file_name": "a.pptx",
                "file_hash": "hash-1",
                "file_size": 1234,
            }
        ],
    }

    with (
        patch(
            "app.auth.connector.get_ray_connection",
            new=AsyncMock(return_value=ray_connection),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch(
            "app.slack.document_mt_quotes.get_document_mt_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch("app.slack.web.download_file", new=AsyncMock()) as mock_download,
        patch(
            "app.ray.submissions.check_and_record_submission_metadata_async",
            new=AsyncMock(return_value=(False, record)),
        ) as mock_record,
        patch(
            "app.slack.listener_actions.document_machine_translate", new=AsyncMock()
        ) as mock_mt,
    ):
        result = await process_document_mt_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id="E27SFGS2W",
            channel_id="C1",
            files=[],
            source_language="en",
            target_languages=["zh-CN"],
            quote_id="quote-1",
        )

    assert result["status"] == "processed"
    mock_download.assert_not_awaited()
    mock_record.assert_awaited_once()
    assert mock_record.await_args.kwargs["file_hash"] == "hash-1"
    mock_mt.assert_awaited_once()
    assert mock_mt.await_args.args[0]["enterprise_id"] == "E27SFGS2W"
    assert mock_mt.await_args.args[1:] == ("grid-1", "en", ["zh-CN"], {"zh-CN": 123})
    assert mock_mt.await_args.kwargs == {
        "quote_id": "quote-1",
        "preflight_task_uuid": "preflight-1",
        "selected_pairs": None,
    }


@pytest.mark.asyncio
async def test_process_document_mt_submission_filters_adjusted_quote_scope():
    """Quote Adjust Request selections scope files, languages and MT event pairs."""
    ray_client = MagicMock()
    ray_client.is_trial = False
    ray_client.id_token = "id-token"
    super_group = MagicMock()
    super_group.verify_organization_uuid = "org-uuid"
    ray_connection = MagicMock()
    ray_connection.client = ray_client
    ray_connection.super_group = [super_group]
    record = MagicMock(id=123)
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()
    session = {
        "quote_id": "quote-1",
        "user_id": "U1",
        "team_id": "T1",
        "channel_id": "C1",
        "source_language": "en",
        "target_languages": ["fr", "de"],
        "preflight_task_uuid": "preflight-1",
        "selected_pairs": ["grid-1:fr"],
        "files": [
            {
                "slack_file_id": "F1",
                "title": "a.docx",
                "file_id": "grid-1",
                "file_name": "a.docx",
                "file_hash": "hash-1",
                "file_size": 1234,
            },
            {
                "slack_file_id": "F2",
                "title": "b.docx",
                "file_id": "grid-2",
                "file_name": "b.docx",
                "file_hash": "hash-2",
                "file_size": 2345,
            },
        ],
    }

    with (
        patch(
            "app.auth.connector.get_ray_connection",
            new=AsyncMock(return_value=ray_connection),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch(
            "app.slack.document_mt_quotes.get_document_mt_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.ray.submissions.check_and_record_submission_metadata_async",
            new=AsyncMock(return_value=(False, record)),
        ) as mock_record,
        patch(
            "app.slack.listener_actions.document_machine_translate", new=AsyncMock()
        ) as mock_mt,
    ):
        result = await process_document_mt_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[],
            source_language="en",
            target_languages=["fr", "de"],
            quote_id="quote-1",
        )

    assert result["status"] == "processed"
    assert result["uploaded_count"] == 1
    # Only the selected grid-1:fr pair is recorded and submitted; grid-1:de and
    # the whole of grid-2 are out of the adjusted scope.
    mock_record.assert_awaited_once()
    assert mock_record.await_args.kwargs["target_language"] == "fr"
    mock_mt.assert_awaited_once()
    assert mock_mt.await_args.args[1:] == ("grid-1", "en", ["fr"], {"fr": 123})
    assert mock_mt.await_args.kwargs == {
        "quote_id": "quote-1",
        "preflight_task_uuid": "preflight-1",
        "selected_pairs": ["grid-1:fr"],
    }


@pytest.mark.asyncio
async def test_process_evaluation_submission_direct_verify_upload():
    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()
    record = MagicMock(id=42)

    with (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=ray_client),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.auth.connector.user_may_receive_quotes",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.docx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.api.verify.get_verify_languages",
            new=AsyncMock(
                return_value=[
                    {"uuid": "src", "code": "en", "name": "English"},
                    {"uuid": "lang-1", "code": "fr", "name": "French"},
                ]
            ),
        ),
        patch(
            "app.ray.submissions.check_and_record_evaluate_submission_async",
            new=AsyncMock(return_value=(False, record)),
        ) as mock_dedupe,
        patch("app.api.verify.submit_evaluation_job", new=AsyncMock()) as mock_submit,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.docx"}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
            preaccepted_ai_translation_quote=True,
            prequote_message_ts="123.456",
            ai_translation_filename_and_languages=["a.docx:lang-1"],
        )

    assert result == {
        "status": "submitted",
        "file_count": 1,
        "duplicate_count": 0,
    }
    mock_dedupe.assert_awaited_once()
    assert mock_dedupe.await_args.kwargs["source_language"] == "en"
    assert mock_dedupe.await_args.kwargs["target_languages"] == ["fr"]
    mock_submit.assert_awaited_once()
    assert mock_submit.await_args.args[:4] == (
        ray_client,
        ["/tmp/a.docx"],
        ["lang-1"],
        "ref",
    )
    assert mock_submit.await_args.kwargs["preaccepted_ai_translation_quote"] is True
    assert mock_submit.await_args.kwargs["prequote_message_ts"] == "123.456"
    assert mock_submit.await_args.kwargs["ai_translation_filename_and_languages"] == [
        "a.docx:lang-1"
    ]


@pytest.mark.asyncio
async def test_process_evaluation_submission_non_admin_skips_pdf_prequote():
    """Non-admins skip PDF pre-quote and use prod-like confirmation_required=False."""
    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()
    record = MagicMock(id=42)

    with (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=ray_client),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.auth.connector.user_may_receive_quotes",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.pdf")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.api.verify.get_verify_languages",
            new=AsyncMock(
                return_value=[
                    {"uuid": "src", "code": "en", "name": "English"},
                    {"uuid": "lang-1", "code": "fr", "name": "French"},
                ]
            ),
        ),
        patch(
            "app.ray.submissions.check_and_record_evaluate_submission_async",
            new=AsyncMock(return_value=(False, record)),
        ),
        patch(
            "app.slack.evaluation_submissions.publish_pdf_evaluate_convert",
            new=AsyncMock(),
        ) as mock_publish,
        patch(
            "app.slack.pdf_evaluate_quotes.save_pdf_evaluate_quote_session",
            new=AsyncMock(),
        ) as mock_save,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pdf", "size": 1000}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
        )

    assert result["status"] == "submitted"
    mock_save.assert_not_awaited()
    mock_publish.assert_awaited_once()
    assert mock_publish.await_args.kwargs["slack_ht_quote_after_qe"] is False
    assert mock_publish.await_args.kwargs["confirmation_required"] is False
    assert mock_publish.await_args.kwargs["workflow_uuid"] is None


@pytest.mark.asyncio
async def test_process_evaluation_submission_non_admin_ht_uses_ht_after_qe():
    """Non-admin HT clears HUMAN_EVALUATION and defers HV until Slack Accept."""
    from app.constants import HUMAN_EVALUATION_WORKFLOW_UUID

    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()
    record = MagicMock(id=42)

    with (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=ray_client),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.auth.connector.user_may_receive_quotes",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.docx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.api.verify.get_verify_languages",
            new=AsyncMock(
                return_value=[
                    {"uuid": "src", "code": "en", "name": "English"},
                    {"uuid": "lang-1", "code": "fr", "name": "French"},
                ]
            ),
        ),
        patch(
            "app.ray.submissions.check_and_record_evaluate_submission_async",
            new=AsyncMock(return_value=(False, record)),
        ),
        patch("app.api.verify.submit_evaluation_job", new=AsyncMock()) as mock_submit,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.docx", "size": 1000}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=HUMAN_EVALUATION_WORKFLOW_UUID,
            job_notes="",
        )

    assert result["status"] == "submitted"
    mock_submit.assert_awaited_once()
    assert mock_submit.await_args.kwargs["slack_ht_quote_after_qe"] is True
    assert mock_submit.await_args.kwargs["confirmation_required"] is True
    assert mock_submit.await_args.kwargs["workflow_uuid"] is None


@pytest.mark.asyncio
async def test_process_evaluation_submission_ht_sa_stamps_requester_email():
    """RAY-81247: HT SA path passes Slack poster email into evaluate/create."""
    from app.constants import HUMAN_EVALUATION_WORKFLOW_UUID

    ht_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()
    record = MagicMock(id=42)

    with (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[{"uuid": "ibm-sg"}]),
        ),
        patch(
            "app.auth.connector.user_may_receive_quotes",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.ibm_ht_service_account.should_use_ht_service_account",
            return_value=True,
        ),
        patch(
            "app.ibm_ht_service_account.get_ht_service_account_ray_client",
            new=AsyncMock(return_value=ht_client),
        ),
        patch(
            "app.ibm_ht_service_account.resolve_slack_poster_email",
            new=AsyncMock(return_value="poster@ibm.com"),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.docx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.api.verify.get_verify_languages",
            new=AsyncMock(
                return_value=[
                    {"uuid": "src", "code": "en", "name": "English"},
                    {"uuid": "lang-1", "code": "fr", "name": "French"},
                ]
            ),
        ),
        patch(
            "app.ray.submissions.check_and_record_evaluate_submission_async",
            new=AsyncMock(return_value=(False, record)),
        ),
        patch("app.api.verify.submit_evaluation_job", new=AsyncMock()) as mock_submit,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id="E-IBM",
            channel_id="C1",
            files=[{"id": "F1", "title": "a.docx", "size": 1000}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=HUMAN_EVALUATION_WORKFLOW_UUID,
            job_notes="",
        )

    assert result["status"] == "submitted"
    mock_submit.assert_awaited_once()
    assert mock_submit.await_args.kwargs["requester_email"] == "poster@ibm.com"
    assert mock_submit.await_args.args[0] is ht_client


@pytest.mark.asyncio
async def test_process_evaluation_submission_admin_ht_clears_fixed_workflow():
    """Admin HT clears HUMAN_EVALUATION so staged AI/QE quotes can run."""
    from app.constants import HUMAN_EVALUATION_WORKFLOW_UUID

    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()
    record = MagicMock(id=42)

    with (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=ray_client),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.auth.connector.user_may_receive_quotes",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.docx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.api.verify.get_verify_languages",
            new=AsyncMock(
                return_value=[
                    {"uuid": "src", "code": "en", "name": "English"},
                    {"uuid": "lang-1", "code": "fr", "name": "French"},
                ]
            ),
        ),
        patch(
            "app.ray.submissions.check_and_record_evaluate_submission_async",
            new=AsyncMock(return_value=(False, record)),
        ),
        patch("app.api.verify.submit_evaluation_job", new=AsyncMock()) as mock_submit,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.docx", "size": 1000}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=HUMAN_EVALUATION_WORKFLOW_UUID,
            job_notes="",
        )

    assert result["status"] == "submitted"
    mock_submit.assert_awaited_once()
    assert mock_submit.await_args.kwargs["confirmation_required"] is True
    assert mock_submit.await_args.kwargs["workflow_uuid"] is None
    assert mock_submit.await_args.kwargs["slack_ht_quote_after_qe"] is False


@pytest.mark.asyncio
async def test_process_evaluation_submission_pdf_requests_extract_quote_before_conversion():
    ray_client = MagicMock()
    ray_client.id = "member-1"
    ray_client.user_group_id = "group-1"
    fake_slack = MagicMock()

    with (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=ray_client),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.auth.connector.user_may_receive_quotes",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch(
            "app.auth.connector.get_group_mt_engine",
            new=AsyncMock(return_value="google"),
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.pdf")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.ray.utils.upload_to_file_server",
            new=AsyncMock(return_value="grid-pdf-1"),
        ) as mock_upload,
        patch(
            "app.api.verify.get_verify_languages",
            new=AsyncMock(
                return_value=[
                    {"uuid": "src", "code": "en", "name": "English"},
                    {"uuid": "lang-1", "code": "fr", "name": "French"},
                    {"uuid": "lang-2", "code": "de", "name": "German"},
                ]
            ),
        ),
        patch(
            "app.slack.pdf_evaluate_quotes.save_pdf_evaluate_quote_session",
            new=AsyncMock(return_value="stable-quote-id"),
        ) as mock_save,
        patch(
            "app.api.stream_proxy.send_document_mt_quote_request",
            new=AsyncMock(),
        ) as mock_quote,
        patch(
            "app.slack.evaluation_submissions.publish_pdf_evaluate_convert",
            new=AsyncMock(),
        ) as mock_publish,
        patch(
            "app.ray.submissions.check_and_record_evaluate_submission_async",
            new=AsyncMock(),
        ) as mock_dedupe,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=True),
        patch("os.path.getsize", return_value=1000),
        patch("os.path.basename", return_value="a.pdf"),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id="E27SFGS2W",
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pdf", "size": 1000}],
            target_langs_uuid=["lang-1", "lang-2"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="notes",
            quote_id="stable-quote-id",
        )

    assert result == {"status": "quote_requested", "quote_id": "stable-quote-id"}
    mock_publish.assert_not_awaited()
    mock_dedupe.assert_not_awaited()
    fake_slack.chat_postMessage.assert_not_called()
    mock_upload.assert_awaited_once()
    mock_save.assert_awaited_once()
    assert mock_save.await_args.kwargs["quote_id"] == "stable-quote-id"
    assert mock_save.await_args.kwargs["stage"] == "quote_pending"
    assert mock_save.await_args.kwargs["files"][0]["gridfs_file_id"] == "grid-pdf-1"
    mock_quote.assert_awaited_once()
    assert mock_quote.await_args.kwargs["output_stream"] == (
        "verify:slack:evaluate:pdf:quote"
    )
    assert mock_quote.await_args.kwargs["target_languages"] == ["fr", "de"]
    assert mock_quote.await_args.kwargs["enterprise_id"] == "E27SFGS2W"
    assert mock_quote.await_args.kwargs["files"] == [
        {"file_id": "grid-pdf-1", "file_name": "a.pdf", "file_size": 1000}
    ]


def _enter_accepted_pdf_patches(
    stack: ExitStack,
    ray_client,
    fake_slack,
    *,
    download_paths: list[str],
) -> None:
    """Stub the I/O boundary for an already-accepted evaluate submission."""
    for patcher in (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=ray_client),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.auth.connector.user_may_receive_quotes",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch(
            "app.slack.web.download_file",
            new=AsyncMock(side_effect=list(download_paths)),
        ),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.api.verify.get_verify_languages",
            new=AsyncMock(
                return_value=[
                    {"uuid": "src", "code": "en", "name": "English"},
                    {"uuid": "lang-1", "code": "fr", "name": "French"},
                ]
            ),
        ),
        patch(
            "app.ray.submissions.check_and_record_evaluate_submission_async",
            new=AsyncMock(return_value=(False, MagicMock(id=42))),
        ),
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        stack.enter_context(patcher)


@pytest.mark.asyncio
async def test_process_evaluation_submission_pdf_accept_keeps_the_pdf():
    """A PDF accept must not drop the PDF from its own batch.

    The accept path keys selections on the name Verify sees after conversion
    (``a.docx``) while the Slack title is still ``a.pdf``. Matching the raw
    title discarded every PDF and then published an empty job, which Verify
    rejected with a 400 and left the quote stuck on "converting".
    """
    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()

    with ExitStack() as stack:
        _enter_accepted_pdf_patches(
            stack, ray_client, fake_slack, download_paths=["/tmp/a.pdf"]
        )
        mock_publish = stack.enter_context(
            patch(
                "app.slack.evaluation_submissions.publish_pdf_evaluate_convert",
                new=AsyncMock(),
            )
        )
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pdf", "size": 1000}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
            preaccepted_ai_translation_quote=True,
            prequote_message_ts="123.456",
            ai_translation_filename_and_languages=["a.docx:lang-1"],
            quote_id="quote-1",
        )

    assert result == {"status": "submitted", "file_count": 1, "duplicate_count": 0}
    mock_publish.assert_awaited_once()
    kwargs = mock_publish.await_args.kwargs
    assert kwargs["input_files"] == ["/tmp/a.pdf"]
    # int-slack-verify-consumer keys conversion off the raw .pdf name.
    assert kwargs["file_titles"] == ["a.pdf"]
    assert kwargs["target_langs_uuid"] == ["lang-1"]
    # Verify sees the converted name, so the pairs must use it.
    assert kwargs["ai_translation_filename_and_languages"] == ["a.docx:lang-1"]


@pytest.mark.asyncio
async def test_process_evaluation_submission_mixed_pdf_and_text_keeps_both():
    """A PDF submitted alongside a non-PDF must not be silently dropped."""
    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()

    with ExitStack() as stack:
        _enter_accepted_pdf_patches(
            stack, ray_client, fake_slack, download_paths=["/tmp/a.pdf", "/tmp/b.txt"]
        )
        mock_publish = stack.enter_context(
            patch(
                "app.slack.evaluation_submissions.publish_pdf_evaluate_convert",
                new=AsyncMock(),
            )
        )
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[
                {"id": "F1", "title": "a.pdf", "size": 1000},
                {"id": "F2", "title": "b.txt", "size": 10},
            ],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
            preaccepted_ai_translation_quote=True,
            prequote_message_ts="123.456",
            ai_translation_filename_and_languages=["a.docx:lang-1", "b.txt:lang-1"],
            quote_id="quote-1",
        )

    assert result["status"] == "submitted"
    assert result["file_count"] == 2
    kwargs = mock_publish.await_args.kwargs
    assert kwargs["input_files"] == ["/tmp/a.pdf", "/tmp/b.txt"]
    assert kwargs["file_titles"] == ["a.pdf", "b.txt"]
    assert sorted(kwargs["ai_translation_filename_and_languages"]) == [
        "a.docx:lang-1",
        "b.txt:lang-1",
    ]


@pytest.mark.asyncio
async def test_process_evaluation_submission_refuses_to_publish_empty_batch():
    """Never publish a job with no files or targets; restore the quote instead."""
    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()

    with ExitStack() as stack:
        _enter_accepted_pdf_patches(
            stack, ray_client, fake_slack, download_paths=["/tmp/a.pdf"]
        )
        mock_publish = stack.enter_context(
            patch(
                "app.slack.evaluation_submissions.publish_pdf_evaluate_convert",
                new=AsyncMock(),
            )
        )
        mock_restore = stack.enter_context(
            patch(
                "app.slack.pdf_evaluate_quotes.restore_pdf_evaluate_quote_for_retry",
                new=AsyncMock(return_value=True),
            )
        )
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pdf", "size": 1000}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
            preaccepted_ai_translation_quote=True,
            prequote_message_ts="123.456",
            ai_translation_filename_and_languages=["someone-elses-file.docx:lang-1"],
            quote_id="quote-1",
        )

    assert result == {"status": "nothing_to_submit"}
    mock_publish.assert_not_awaited()
    mock_restore.assert_awaited_once()
    assert mock_restore.await_args.kwargs["quote_id"] == "quote-1"
    assert mock_restore.await_args.kwargs["message_ts"] == "123.456"
    fake_slack.chat_postMessage.assert_awaited()
    assert fake_slack.chat_postMessage.await_args.kwargs["channel"] == "U1"


@pytest.mark.asyncio
async def test_process_evaluation_submission_refuses_post_convert_filename_collision():
    """Same-stem PDF+DOCX must not publish convert; restore the quote instead."""
    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()

    with ExitStack() as stack:
        _enter_accepted_pdf_patches(
            stack,
            ray_client,
            fake_slack,
            download_paths=["/tmp/a.pdf", "/tmp/a.docx"],
        )
        mock_publish = stack.enter_context(
            patch(
                "app.slack.evaluation_submissions.publish_pdf_evaluate_convert",
                new=AsyncMock(),
            )
        )
        mock_restore = stack.enter_context(
            patch(
                "app.slack.pdf_evaluate_quotes.restore_pdf_evaluate_quote_for_retry",
                new=AsyncMock(return_value=True),
            )
        )
        mock_mark_failed = stack.enter_context(
            patch(
                "app.ray.submissions.updated_submission_status",
            )
        )
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[
                {"id": "F1", "title": "A great summer vacation.pdf", "size": 1000},
                {"id": "F2", "title": "A great summer vacation.docx", "size": 1000},
            ],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
            preaccepted_ai_translation_quote=True,
            prequote_message_ts="123.456",
            ai_translation_filename_and_languages=[
                "a.docx:lang-1",
            ],
            quote_id="quote-1",
        )

    assert result == {"status": "filename_collision"}
    mock_publish.assert_not_awaited()
    mock_restore.assert_awaited_once()
    assert mock_restore.await_args.kwargs["quote_id"] == "quote-1"
    assert mock_restore.await_args.kwargs["message_ts"] == "123.456"
    status = mock_restore.await_args.kwargs["status_message"]
    assert "a.pdf" in status
    assert "a.docx" in status
    fake_slack.chat_postMessage.assert_awaited()
    dm = fake_slack.chat_postMessage.await_args.kwargs
    assert dm["channel"] == "U1"
    assert "a.pdf" in dm["text"]
    mock_mark_failed.assert_called()


@pytest.mark.asyncio
async def test_process_evaluation_submission_verify_api_error_posts_permission_message():
    from app.api.verify import VerifyAPIError

    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()
    record = MagicMock(id=99)

    with (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=ray_client),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.auth.connector.user_may_receive_quotes",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.docx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.api.verify.get_verify_languages",
            new=AsyncMock(
                return_value=[
                    {"uuid": "src", "code": "en", "name": "English"},
                    {"uuid": "lang-1", "code": "fr", "name": "French"},
                ]
            ),
        ),
        patch(
            "app.ray.submissions.check_and_record_evaluate_submission_async",
            new=AsyncMock(return_value=(False, record)),
        ),
        patch(
            "app.ray.submissions.updated_submission_status",
            return_value=True,
        ) as mock_fail_status,
        patch(
            "app.api.verify.submit_evaluation_job",
            new=AsyncMock(side_effect=VerifyAPIError("Permission denied")),
        ),
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.docx"}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
        )

    assert result == {"status": "permission_denied"}
    fake_slack.chat_postMessage.assert_awaited_once()
    assert "permission" in fake_slack.chat_postMessage.await_args.kwargs["text"].lower()
    assert (
        "administrator" in fake_slack.chat_postMessage.await_args.kwargs["text"].lower()
    )
    mock_fail_status.assert_called_once()
    assert mock_fail_status.call_args.kwargs["submission_id"] == 99


@pytest.mark.asyncio
async def test_process_evaluation_submission_uses_downloaded_name_when_slack_title_truncated():
    """RAY-81396: Verify pair keys must use the downloaded filename, not the
    75-character Slack picker label."""
    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()
    record = MagicMock(id=42)
    downloaded = (
        "/tmp/F1-xxxx/Anlage 1 IBM 2014 Employees Stock Purchase Plan "
        "Prospectus Revised as of Jun 16 2025.docx"
    )
    truncated_title = (
        "Anlage 1 IBM 2014 Employees Stock Purchase Plan Prospectus Revised as of J…"
    )

    with (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=ray_client),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.auth.connector.user_may_receive_quotes",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value=downloaded)),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.api.verify.get_verify_languages",
            new=AsyncMock(
                return_value=[
                    {"uuid": "src", "code": "en", "name": "English"},
                    {"uuid": "lang-1", "code": "de", "name": "German"},
                ]
            ),
        ),
        patch(
            "app.ray.submissions.check_and_record_evaluate_submission_async",
            new=AsyncMock(return_value=(False, record)),
        ),
        patch("app.api.verify.submit_evaluation_job", new=AsyncMock()) as mock_submit,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": truncated_title, "size": 102384}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
            preaccepted_ai_translation_quote=True,
        )

    assert result["status"] == "submitted"
    expected_name = (
        "Anlage 1 IBM 2014 Employees Stock Purchase Plan Prospectus "
        "Revised as of Jun 16 2025.docx"
    )
    assert mock_submit.await_args.kwargs["ai_translation_filename_and_languages"] == [
        f"{expected_name}:lang-1"
    ]


@pytest.mark.asyncio
async def test_process_evaluation_submission_truncated_pdf_title_still_prequotes():
    """RAY-81396: a truncated Slack title without .pdf must still take the PDF
    extract-quote path once the file is downloaded."""
    ray_client = MagicMock()
    ray_client.id = "member-1"
    ray_client.user_group_id = "group-1"
    fake_slack = MagicMock()
    downloaded = (
        "/tmp/F1-xxxx/Anlage 1 IBM 2014 Employees Stock Purchase Plan "
        "Prospectus Revised as of Jun 16 2025.pdf"
    )
    truncated_title = (
        "Anlage 1 IBM 2014 Employees Stock Purchase Plan Prospectus Revised as of J…"
    )

    with (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=ray_client),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.auth.connector.user_may_receive_quotes",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch(
            "app.auth.connector.get_group_mt_engine",
            new=AsyncMock(return_value="google"),
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value=downloaded)),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.ray.utils.upload_to_file_server",
            new=AsyncMock(return_value="grid-pdf-1"),
        ),
        patch(
            "app.api.verify.get_verify_languages",
            new=AsyncMock(
                return_value=[
                    {"uuid": "src", "code": "en", "name": "English"},
                    {"uuid": "lang-1", "code": "de", "name": "German"},
                ]
            ),
        ),
        patch(
            "app.slack.pdf_evaluate_quotes.save_pdf_evaluate_quote_session",
            new=AsyncMock(return_value="stable-quote-id"),
        ),
        patch(
            "app.api.stream_proxy.send_document_mt_quote_request",
            new=AsyncMock(),
        ) as mock_quote,
        patch(
            "app.slack.evaluation_submissions.publish_pdf_evaluate_convert",
            new=AsyncMock(),
        ) as mock_publish,
        patch(
            "app.ray.submissions.check_and_record_evaluate_submission_async",
            new=AsyncMock(),
        ) as mock_dedupe,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=True),
        patch("os.path.getsize", return_value=410936),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": truncated_title, "size": 410936}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
        )

    assert result["status"] == "quote_requested"
    mock_dedupe.assert_not_awaited()
    mock_publish.assert_not_awaited()
    mock_quote.assert_awaited_once()
    assert mock_quote.await_args.kwargs["files"][0]["file_name"].endswith(".pdf")


@pytest.mark.asyncio
async def test_process_evaluation_submission_verify_400_marks_failed_without_retry():
    """RAY-81396: evaluate/create 400 must unlock 24h dedupe instead of
    leaving the row created so SAQ retry posts a false duplicate DM."""
    from app.api.verify import VerifyCreateRejected

    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()
    record = MagicMock(id=58711)

    with (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=ray_client),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.auth.connector.user_may_receive_quotes",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.docx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.api.verify.get_verify_languages",
            new=AsyncMock(
                return_value=[
                    {"uuid": "src", "code": "en", "name": "English"},
                    {"uuid": "lang-1", "code": "de", "name": "German"},
                ]
            ),
        ),
        patch(
            "app.ray.submissions.check_and_record_evaluate_submission_async",
            new=AsyncMock(return_value=(False, record)),
        ),
        patch(
            "app.ray.submissions.updated_submission_status",
            return_value=True,
        ) as mock_fail_status,
        patch(
            "app.api.verify.submit_evaluation_job",
            new=AsyncMock(side_effect=VerifyCreateRejected(400, "filename mismatch")),
        ),
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.docx"}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
        )

    assert result == {"status": "verify_rejected"}
    mock_fail_status.assert_called_once()
    assert mock_fail_status.call_args.kwargs["submission_id"] == 58711
    assert (
        mock_fail_status.call_args.kwargs["processing_status"]
        == SubmissionStatus.FAILED
    )
    fake_slack.chat_postMessage.assert_awaited_once()
    assert "try again" in fake_slack.chat_postMessage.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_process_evaluation_submission_long_filename_tells_user_and_skips_dedupe():
    """RAY-81396: names over 255 chars/bytes must not create a 24h row."""
    from app.ray.utils import SlackFilenameTooLong

    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()
    long_name = "a" * 252 + ".docx"

    with (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=ray_client),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.auth.connector.user_may_receive_quotes",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch(
            "app.slack.web.download_file",
            new=AsyncMock(side_effect=SlackFilenameTooLong(long_name)),
        ),
        patch(
            "app.ray.submissions.check_and_record_evaluate_submission_async",
            new=AsyncMock(),
        ) as mock_dedupe,
        patch("app.api.verify.submit_evaluation_job", new=AsyncMock()) as mock_submit,
        patch("app.saq_jobs.tasks._safe_unlink"),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a…", "size": 1024}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
        )

    assert result == {"status": "no_valid_files"}
    mock_dedupe.assert_not_awaited()
    mock_submit.assert_not_awaited()
    fake_slack.chat_postMessage.assert_awaited_once()
    posted = fake_slack.chat_postMessage.await_args.kwargs["text"]
    assert long_name in posted
    assert "255" in posted
    assert "Rename" in posted


@pytest.mark.asyncio
async def test_process_evaluation_submission_all_duplicates_skips_verify():
    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()
    existing = MagicMock(id=7)

    with (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=ray_client),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.auth.connector.user_may_receive_quotes",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.docx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.api.verify.get_verify_languages",
            new=AsyncMock(
                return_value=[
                    {"uuid": "src", "code": "en", "name": "English"},
                    {"uuid": "lang-1", "code": "fr", "name": "French"},
                ]
            ),
        ),
        patch(
            "app.ray.submissions.check_and_record_evaluate_submission_async",
            new=AsyncMock(return_value=(True, existing)),
        ),
        patch("app.api.verify.submit_evaluation_job", new=AsyncMock()) as mock_submit,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.docx"}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
        )

    assert result == {"status": "duplicate", "duplicate_count": 1}
    mock_submit.assert_not_awaited()
    fake_slack.chat_postMessage.assert_awaited_once()
    assert fake_slack.chat_postMessage.await_args.kwargs["channel"] == "U1"
    message_text = fake_slack.chat_postMessage.await_args.kwargs["text"].lower()
    assert "duplicate" in message_text
    assert "human translation request" in message_text
    assert "quality evaluation" not in message_text


@pytest.mark.asyncio
async def test_process_evaluation_submission_different_target_set_not_duplicate():
    """Overlapping-but-different target sets are submitted in full (set-based dedupe)."""
    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()
    new_record = MagicMock(id=2)

    with (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=ray_client),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.auth.connector.user_may_receive_quotes",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.docx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.api.verify.get_verify_languages",
            new=AsyncMock(
                return_value=[
                    {"uuid": "src", "code": "en", "name": "English"},
                    {"uuid": "lang-fr", "code": "fr", "name": "French"},
                    {"uuid": "lang-de", "code": "de", "name": "German"},
                    {"uuid": "lang-es", "code": "es", "name": "Spanish"},
                ]
            ),
        ),
        patch(
            "app.ray.submissions.check_and_record_evaluate_submission_async",
            new=AsyncMock(return_value=(False, new_record)),
        ) as mock_dedupe,
        patch("app.api.verify.submit_evaluation_job", new=AsyncMock()) as mock_submit,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.docx"}],
            target_langs_uuid=["lang-fr", "lang-es"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
        )

    assert result == {
        "status": "submitted",
        "file_count": 1,
        "duplicate_count": 0,
    }
    mock_dedupe.assert_awaited_once()
    assert mock_dedupe.await_args.kwargs["target_languages"] == ["fr", "es"]
    mock_submit.assert_awaited_once()
    assert mock_submit.await_args.args[2] == ["lang-fr", "lang-es"]
    fake_slack.chat_postMessage.assert_not_awaited()


# --------------------------------------------------------------------------- #
# slack_upload_mt_result
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_slack_upload_mt_result_happy_path(slack_user):
    success_data = {
        "task_uuid": str(uuid4()),
        "file_id": "file-1",
        "tokens": 0,
        "client_id": slack_user.ray_client_id,
        "target_language": "fr",
        "channel_id": "C123",
    }

    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch(
            "app.saq_jobs.tasks.update_slack_job", new=AsyncMock()
        ) as mock_update_job,
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/foo", "file_name": "out.docx"}),
        ),
        patch(
            "app.saq_jobs.tasks.delete_from_file_server", new=AsyncMock()
        ) as mock_delete,
        patch(
            "app.slack.listener_actions.get_language_name",
            new=AsyncMock(return_value="French"),
        ),
        patch(
            "app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()
        ) as mock_upload,
        patch("app.saq_jobs.tasks._safe_unlink"),
    ):
        result = await slack_upload_mt_result(_ctx(), success_data=success_data)

    assert result == {"status": "delivered", "task_uuid": success_data["task_uuid"]}
    mock_upload.assert_awaited_once()
    mock_delete.assert_awaited_once_with("file-1")
    # First call marks slack_uploading, second marks delivered.
    assert mock_update_job.await_count == 2


@pytest.mark.asyncio
async def test_slack_upload_mt_result_no_slack_user_returns_no_user_status():
    task_uuid = str(uuid4())
    client_id = str(uuid4())
    success_data = {
        "task_uuid": task_uuid,
        "file_id": "file-1",
        "tokens": 0,
        "client_id": client_id,
        "target_language": "fr",
        "channel_id": "C123",
        "team_id": "TTEAM",
        "slack_user_id": "UUSER",
        "submission_id": 99123,
    }

    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.saq_jobs.tasks.update_slack_job", new=AsyncMock()
        ) as mock_update_job,
        patch("app.saq_jobs.tasks.updated_submission_status") as mock_submission_status,
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        result = await slack_upload_mt_result(_ctx(), success_data=success_data)

    assert result["status"] == "no_slack_user"
    mock_update_job.assert_any_await(task_uuid=task_uuid, status="failed_delivery")
    assert mock_submission_status.call_args_list[-1].kwargs == {
        "submission_id": 99123,
        "processing_status": SubmissionStatus.FAILED,
    }
    mock_notify.assert_called()
    notify_args, notify_kwargs = mock_notify.call_args
    assert notify_args[1] == "Document MT Slack delivery failed (no_slack_user)"
    assert notify_kwargs["extra"]["task_uuid"] == task_uuid
    assert notify_kwargs["extra"]["client_id"] == client_id
    assert notify_kwargs["extra"]["team_id"] == "TTEAM"
    assert notify_kwargs["extra"]["slack_user_id"] == "UUSER"


@pytest.mark.asyncio
async def test_slack_upload_mt_result_re_raises_for_saq_retry(slack_user):
    """A failed upload must re-raise so SAQ can retry the job."""
    success_data = {
        "task_uuid": str(uuid4()),
        "file_id": "file-1",
        "tokens": 0,
        "client_id": slack_user.ray_client_id,
        "target_language": "fr",
        "channel_id": "C123",
    }

    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch("app.saq_jobs.tasks.update_slack_job", new=AsyncMock()),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/foo", "file_name": "out.docx"}),
        ),
        patch(
            "app.slack.listener_actions.get_language_name",
            new=AsyncMock(return_value="French"),
        ),
        patch(
            "app.slack.web.upload_file_to_slack_memory_efficient",
            new=AsyncMock(side_effect=RuntimeError("file_update_failed")),
        ),
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        with pytest.raises(RuntimeError):
            await slack_upload_mt_result(_ctx(), success_data=success_data)

    # Retryable attempt: do not page yet.
    mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_slack_upload_mt_result_alerts_on_final_delivery_failure(slack_user):
    """Final failed Slack upload pages Google Chat / BugLog with delivery context."""
    task_uuid = str(uuid4())
    success_data = {
        "task_uuid": task_uuid,
        "file_id": "file-1",
        "tokens": 0,
        "client_id": slack_user.ray_client_id,
        "target_language": "fr",
        "channel_id": "C123",
        "team_id": "TTEAM",
        "slack_user_id": "UUSER",
        "submission_id": 42,
    }

    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch(
            "app.saq_jobs.tasks.update_slack_job", new=AsyncMock()
        ) as mock_update_job,
        patch("app.saq_jobs.tasks.updated_submission_status") as mock_submission_status,
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/foo", "file_name": "out.docx"}),
        ),
        patch(
            "app.slack.listener_actions.get_language_name",
            new=AsyncMock(return_value="French"),
        ),
        patch(
            "app.slack.web.upload_file_to_slack_memory_efficient",
            new=AsyncMock(side_effect=RuntimeError("file_update_failed")),
        ),
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        with pytest.raises(RuntimeError):
            await slack_upload_mt_result(
                _ctx(retryable=False), success_data=success_data
            )

    mock_update_job.assert_any_await(task_uuid=task_uuid, status="failed_delivery")
    assert mock_submission_status.call_args_list[-1].kwargs == {
        "submission_id": 42,
        "processing_status": SubmissionStatus.FAILED,
    }
    assert any(
        call.args
        and len(call.args) > 1
        and call.args[1] == "Document MT Slack delivery failed (final attempt)"
        for call in mock_notify.call_args_list
    )


# --------------------------------------------------------------------------- #
# slack_upload_transcription
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_slack_upload_transcription_renames_temp_file_for_extension(slack_user):
    """The temp download is renamed to the expected filename so Slack keeps the extension."""
    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/abc/raw"}),
        ),
        patch(
            "app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()
        ) as mock_upload,
        patch("os.rename") as mock_rename,
        patch("app.saq_jobs.tasks._safe_unlink"),
    ):
        await slack_upload_transcription(
            _ctx(),
            file_id="f1",
            file_name="result.srt",
            task_uuid=str(uuid4()),
            pipeline_type="transcribe",
            client_id=slack_user.ray_client_id,
            channel_id="C123",
            thread_ts="123.0",
            team_id="T1",
            slack_user_id="U1",
        )

    mock_rename.assert_called_once_with("/tmp/abc/raw", "/tmp/abc/result.srt")
    mock_upload.assert_awaited_once()
    assert mock_upload.await_args.kwargs["filename"] == "result.srt"
    assert mock_upload.await_args.kwargs["thread_ts"] == "123.0"


@pytest.mark.asyncio
async def test_slack_upload_transcription_posts_follow_up_message(slack_user):
    fake_client = MagicMock()
    fake_client.chat_postMessage = AsyncMock()

    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/abc/result.srt"}),
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_client),
        patch("app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()),
        patch("os.rename"),
        patch("app.saq_jobs.tasks._safe_unlink"),
    ):
        await slack_upload_transcription(
            _ctx(),
            file_id="f1",
            file_name="result.srt",
            task_uuid=str(uuid4()),
            pipeline_type="transcribe",
            client_id=slack_user.ray_client_id,
            channel_id="C123",
            thread_ts=None,
            follow_up_message="Edit and reupload.",
        )

    fake_client.chat_postMessage.assert_awaited_once()
    assert (
        fake_client.chat_postMessage.await_args.kwargs["text"] == "Edit and reupload."
    )


@pytest.mark.asyncio
async def test_slack_upload_transcription_posts_srt_review_after_file(slack_user):
    fake_client = MagicMock()
    fake_client.chat_postMessage = AsyncMock()

    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/abc/result.srt"}),
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_client),
        patch("app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()),
        patch("os.rename"),
        patch("app.saq_jobs.tasks._safe_unlink"),
    ):
        await slack_upload_transcription(
            _ctx(),
            file_id="f1",
            file_name="result.srt",
            task_uuid=str(uuid4()),
            pipeline_type="transcribe",
            client_id=slack_user.ray_client_id,
            channel_id="C123",
            thread_ts="123.0",
            srt_review_quote_id="q1",
        )

    assert fake_client.chat_postMessage.await_count == 2
    posted_calls = fake_client.chat_postMessage.await_args_list
    assert all(call.kwargs["thread_ts"] == "123.0" for call in posted_calls)
    replace_ids = [
        el.get("action_id")
        for block in posted_calls[0].kwargs.get("blocks") or []
        for el in block.get("elements", [])
    ]
    approve_ids = [
        el.get("action_id")
        for block in posted_calls[1].kwargs.get("blocks") or []
        for el in block.get("elements", [])
    ]
    assert "media_srt_replace" in replace_ids
    assert "media_srt_approve_continue" not in replace_ids
    assert "media_srt_approve_continue" in approve_ids
    assert "media_srt_replace" not in approve_ids


@pytest.mark.asyncio
async def test_slack_upload_transcription_posts_srt_review_when_download_fails(
    slack_user,
):
    fake_client = MagicMock()
    fake_client.chat_postMessage = AsyncMock()

    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": None}),
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_client),
        patch("app.saq_jobs.tasks.notify_exception"),
        patch("app.saq_jobs.tasks._safe_unlink"),
    ):
        result = await slack_upload_transcription(
            _ctx(),
            file_id="f1",
            file_name="result.srt",
            task_uuid=str(uuid4()),
            pipeline_type="transcribe",
            client_id=slack_user.ray_client_id,
            channel_id="C123",
            thread_ts="123.0",
            srt_review_quote_id="q1",
        )

    assert result["status"] == "download_failed"
    action_ids = [
        el.get("action_id")
        for block in fake_client.chat_postMessage.await_args.kwargs.get("blocks") or []
        for el in block.get("elements", [])
    ]
    assert "media_srt_approve_continue" in action_ids


@pytest.mark.asyncio
async def test_slack_upload_transcription_posts_srt_review_on_final_retry(
    slack_user,
):
    fake_client = MagicMock()
    fake_client.chat_postMessage = AsyncMock()

    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/abc/result.srt"}),
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_client),
        patch(
            "app.slack.web.upload_file_to_slack_memory_efficient",
            new=AsyncMock(side_effect=RuntimeError("slack down")),
        ),
        patch("os.rename"),
        patch("app.saq_jobs.tasks.notify_exception"),
        patch("app.saq_jobs.tasks._safe_unlink"),
    ):
        with pytest.raises(RuntimeError, match="slack down"):
            await slack_upload_transcription(
                _ctx(retryable=False),
                file_id="f1",
                file_name="result.srt",
                task_uuid=str(uuid4()),
                pipeline_type="transcribe",
                client_id=slack_user.ray_client_id,
                channel_id="C123",
                thread_ts="123.0",
                srt_review_quote_id="q1",
            )

    action_ids = [
        el.get("action_id")
        for block in fake_client.chat_postMessage.await_args.kwargs.get("blocks") or []
        for el in block.get("elements", [])
    ]
    assert "media_srt_approve_continue" in action_ids


@pytest.mark.asyncio
async def test_slack_upload_transcription_no_slack_user_short_circuits():
    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=None),
        ),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        result = await slack_upload_transcription(
            _ctx(),
            file_id="f1",
            file_name="x.srt",
            task_uuid="t1",
            pipeline_type="transcribe",
            client_id="missing",
            channel_id="C1",
            thread_ts=None,
            team_id="T1",
            slack_user_id="U1",
        )
    assert result["status"] == "no_slack_user"
    assert "no_slack_user" in mock_notify.call_args.args[1]


@pytest.mark.asyncio
async def test_slack_upload_transcription_fails_submissions_when_review_undeliverable():
    extra = {
        "media_quote_id": "q1",
        "submission_ids": [42],
        "workflow_type": "transcribe_translate",
    }
    task = SimpleNamespace(extra_data=extra)
    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=None),
        ),
        patch("app.saq_jobs.tasks.notify_exception"),
        patch(
            "app.transcriber_tasks.tasks.get_transcription_task",
            new=AsyncMock(return_value=task),
        ),
        patch(
            "app.ray.events.media_pipeline_events.fail_media_submissions",
            new=AsyncMock(),
        ) as mock_fail,
        patch(
            "app.ray.events.media_pipeline_events.mark_media_quote_cancelled",
            new=AsyncMock(),
        ) as mock_cancel,
    ):
        result = await slack_upload_transcription(
            _ctx(),
            file_id="f1",
            file_name="x.srt",
            task_uuid="t1",
            pipeline_type="transcribe",
            client_id="missing",
            channel_id="C1",
            thread_ts=None,
            srt_review_quote_id="q1",
        )

    assert result["status"] == "no_slack_user"
    mock_fail.assert_awaited_once_with(extra)
    mock_cancel.assert_awaited_once_with(extra)


# --------------------------------------------------------------------------- #
# slack_upload_verify_complete
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_slack_upload_verify_complete_happy_path(slack_user):
    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/x", "file_name": "qe.xlsx"}),
        ),
        patch(
            "app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()
        ) as mock_upload,
        patch("app.saq_jobs.tasks._safe_unlink"),
    ):
        result = await slack_upload_verify_complete(
            _ctx(),
            grid_file_id="grid-1",
            client_id=slack_user.ray_client_id,
            channel_id="C123",
            team_id="T1",
            slack_user_id="U1",
        )

    assert result == {"status": "delivered"}
    mock_upload.assert_awaited_once()
    assert mock_upload.await_args.kwargs["filename"] == "qe.xlsx"


@pytest.mark.asyncio
async def test_slack_upload_verify_complete_no_slack_user():
    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=None),
        ),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        result = await slack_upload_verify_complete(
            _ctx(),
            grid_file_id="grid-1",
            client_id="ht-sa-uuid",
            channel_id="C123",
            team_id="T1",
            slack_user_id="U1",
        )

    assert result == {"status": "no_slack_user"}
    mock_notify.assert_called_once()
    assert "no_slack_user" in mock_notify.call_args.args[1]
    assert mock_notify.call_args.kwargs["extra"]["client_id"] == "ht-sa-uuid"
    assert mock_notify.call_args.kwargs["extra"]["slack_user_id"] == "U1"


# --------------------------------------------------------------------------- #
# persist_log_notification
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_persist_log_notification_delegates_to_log_notification():
    log = AsyncMock()
    with patch("app.ray.events.logging.log_notification", new=log):
        result = await persist_log_notification(
            _ctx(),
            event="ray:client:signup",
            event_data={"x": 1},
            user_id="U1",
            channel_id="C1",
            ray_client_id="R1",
            message="ClientSignupEventMessage",
        )

    assert result["status"] == "logged"
    log.assert_awaited_once()
    assert log.await_args.kwargs["event"] == "ray:client:signup"


# --------------------------------------------------------------------------- #
# charge_inline_mt_usage (RAY-80258)
# --------------------------------------------------------------------------- #


def _billing() -> dict[str, Any]:
    return {
        "client_id": str(uuid4()),
        "text_length": 100,
        "target_languages": ["fr"],
        "usage_type": "channel_translation",
        "idempotency_key": "key-abc",
        "group_uuid": "billing-group",
    }


def _usage_log() -> dict[str, Any]:
    return {
        "user_uuid": "client-1",
        "group_uuid": "billing-group",
        "organization_uuid": "org-1",
        "input_text": "hello",
        "source_lang": "en",
        "translations": {"fr": "bonjour"},
        "usage_type": "channel_translation",
    }


@pytest.mark.asyncio
async def test_charge_inline_mt_usage_happy_path():
    """Charges the gateway then logs usage against the returned transaction."""
    billing = _billing()
    usage_log = _usage_log()
    charge = AsyncMock(return_value="txn-123")
    log_usage = AsyncMock()
    with (
        patch("app.auth.connector.log_inline_mt_usage_by_client_id", new=charge),
        patch("app.mt.logs.log_google_api_usage", new=log_usage),
    ):
        result = await charge_inline_mt_usage(
            _ctx(), billing=billing, usage_log=usage_log
        )

    assert result == {"status": "charged", "transaction_uuid": "txn-123"}
    charge.assert_awaited_once()
    assert charge.await_args.kwargs["idempotency_key"] == "key-abc"
    assert charge.await_args.kwargs["client_id"] == billing["client_id"]
    # Google API usage row carries the gateway transaction uuid.
    log_usage.assert_awaited_once()
    assert log_usage.await_args.kwargs["transaction_uuid"] == "txn-123"
    assert log_usage.await_args.kwargs["group_uuid"] == "billing-group"


@pytest.mark.asyncio
async def test_charge_inline_mt_usage_retries_on_connect_timeout():
    """A transient ConnectTimeout is retried inside the task before logging."""
    charge = AsyncMock(side_effect=[httpx.ConnectTimeout("boom"), "txn-after-retry"])
    log_usage = AsyncMock()
    with (
        patch("app.auth.connector.log_inline_mt_usage_by_client_id", new=charge),
        patch("app.mt.logs.log_google_api_usage", new=log_usage),
        # Skip the backoff sleep so the test stays fast.
        patch("app.api.http_client.asyncio.sleep", new=AsyncMock()),
    ):
        result = await charge_inline_mt_usage(
            _ctx(), billing=_billing(), usage_log=_usage_log()
        )

    assert result["transaction_uuid"] == "txn-after-retry"
    assert charge.await_count == 2
    log_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_charge_inline_mt_usage_notifies_on_final_attempt():
    """On the final, non-retryable attempt the billing failure raises a BugLog alert."""
    charge = AsyncMock(side_effect=RuntimeError("gateway 500"))
    with (
        patch("app.auth.connector.log_inline_mt_usage_by_client_id", new=charge),
        patch("app.mt.logs.log_google_api_usage", new=AsyncMock()),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        with pytest.raises(RuntimeError):
            await charge_inline_mt_usage(
                _ctx(retryable=False),
                billing=_billing(),
                usage_log=_usage_log(),
            )

    mock_notify.assert_called_once()
    # The billing-specific alert carries the idempotency key for replay.
    assert mock_notify.call_args.kwargs["extra"]["idempotency_key"] == "key-abc"


@pytest.mark.asyncio
async def test_charge_inline_mt_usage_reraises_without_alert_when_retryable():
    """A retryable failure re-raises so SAQ retries, but does not alert yet."""
    charge = AsyncMock(side_effect=RuntimeError("transient"))
    with (
        patch("app.auth.connector.log_inline_mt_usage_by_client_id", new=charge),
        patch("app.mt.logs.log_google_api_usage", new=AsyncMock()),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        with pytest.raises(RuntimeError):
            await charge_inline_mt_usage(
                _ctx(retryable=True),
                billing=_billing(),
                usage_log=_usage_log(),
            )

    mock_notify.assert_not_called()


# --------------------------------------------------------------------------- #
# charge_document_mt + deferred enqueue (RAY-80417)
# --------------------------------------------------------------------------- #


def _mt_charge() -> dict[str, Any]:
    return {
        "text_length": 100,
        "word_count": 20,
        "engine": "google",
        "target_languages": ["fr"],
        "source_language": "en",
        "app_name": "slack",
        "file_name": "out.docx",
        "idempotency_key": "slack:task-1:document_translation:fr:characters",
        "submission_group_uuid": "task-1",
        "pdf_conversion_page_count": 3,
        "pdf_idempotency_key": "slack:task-1:pdf_conversion_fee:pages",
    }


@pytest.mark.asyncio
async def test_slack_upload_mt_result_enqueues_document_mt_charge(slack_user):
    """After delivery, a Slack mt_charge payload is enqueued for billing."""
    charge = _mt_charge()
    slack_user.user_id = "U123"
    slack_user.ray_user_group_id = "billing-group-1"
    success_data = {
        "task_uuid": str(uuid4()),
        "file_id": "file-1",
        "tokens": 0,
        "client_id": slack_user.ray_client_id,
        "target_language": "fr",
        "channel_id": "C123",
        "mt_charge": charge,
    }
    fake_slack = MagicMock()
    fake_slack.users_info = AsyncMock(
        return_value={
            "user": {
                "profile": {
                    "email": "poster@example.com",
                    "real_name": "Poster Name",
                }
            }
        }
    )
    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.saq_jobs.tasks.update_slack_job", new=AsyncMock()),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/foo", "file_name": "out.docx"}),
        ),
        patch("app.saq_jobs.tasks.delete_from_file_server", new=AsyncMock()),
        patch(
            "app.slack.listener_actions.get_language_name",
            new=AsyncMock(return_value="French"),
        ),
        patch("app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()),
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch(
            "app.saq_jobs.dispatch.enqueue_document_mt_charge", new=AsyncMock()
        ) as mock_enqueue,
    ):
        result = await slack_upload_mt_result(_ctx(), success_data=success_data)

    assert result["status"] == "delivered"
    mock_enqueue.assert_awaited_once()
    kwargs = mock_enqueue.await_args.kwargs
    assert kwargs["client_id"] == slack_user.ray_client_id
    assert kwargs["idempotency_key"] == charge["idempotency_key"]
    assert kwargs["charge"]["group_uuid"] == "billing-group-1"
    assert kwargs["charge"]["email"] == "poster@example.com"
    assert kwargs["charge"]["client_name"] == "Poster Name"
    for key, value in charge.items():
        assert kwargs["charge"][key] == value


@pytest.mark.asyncio
async def test_slack_upload_mt_result_no_charge_when_absent(slack_user):
    """Teams (or no-conversion) deliveries carry no mt_charge, so nothing is billed."""
    success_data = {
        "task_uuid": str(uuid4()),
        "file_id": "file-1",
        "tokens": 0,
        "client_id": slack_user.ray_client_id,
        "target_language": "fr",
        "channel_id": "C123",
    }
    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch("app.saq_jobs.tasks.update_slack_job", new=AsyncMock()),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/foo", "file_name": "out.docx"}),
        ),
        patch("app.saq_jobs.tasks.delete_from_file_server", new=AsyncMock()),
        patch(
            "app.slack.listener_actions.get_language_name",
            new=AsyncMock(return_value="French"),
        ),
        patch("app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()),
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch(
            "app.saq_jobs.dispatch.enqueue_document_mt_charge", new=AsyncMock()
        ) as mock_enqueue,
    ):
        result = await slack_upload_mt_result(_ctx(), success_data=success_data)

    assert result["status"] == "delivered"
    mock_enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_slack_upload_mt_result_uses_event_poster_id_for_billing(slack_user):
    """Org fallback may leave user_id as org uuid; prefer event slack_user_id."""
    charge = _mt_charge()
    org_uuid = str(uuid4())
    slack_user.user_id = org_uuid
    slack_user.ray_user_group_id = "billing-group-1"
    success_data = {
        "task_uuid": str(uuid4()),
        "file_id": "file-1",
        "tokens": 0,
        "client_id": slack_user.ray_client_id,
        "target_language": "et",
        "channel_id": "D123",
        "mt_charge": charge,
        "team_id": "T123",
        "slack_user_id": "UPOSTER1",
    }
    fake_slack = MagicMock()
    fake_slack.users_info = AsyncMock(
        return_value={
            "user": {
                "profile": {
                    "email": "poster@example.com",
                    "real_name": "Poster Name",
                }
            }
        }
    )
    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ) as mock_resolve,
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.saq_jobs.tasks.update_slack_job", new=AsyncMock()),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/foo", "file_name": "out.docx"}),
        ),
        patch("app.saq_jobs.tasks.delete_from_file_server", new=AsyncMock()),
        patch(
            "app.slack.listener_actions.get_language_name",
            new=AsyncMock(return_value="Estonian"),
        ),
        patch("app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()),
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch(
            "app.saq_jobs.dispatch.enqueue_document_mt_charge", new=AsyncMock()
        ) as mock_enqueue,
    ):
        result = await slack_upload_mt_result(_ctx(), success_data=success_data)

    assert result["status"] == "delivered"
    mock_resolve.assert_awaited_once_with(
        slack_user.ray_client_id,
        team_id="T123",
        slack_user_id="UPOSTER1",
    )
    fake_slack.users_info.assert_awaited_once_with(user="UPOSTER1")
    assert mock_enqueue.await_args.kwargs["charge"]["email"] == "poster@example.com"


@pytest.mark.asyncio
async def test_slack_upload_mt_result_enriches_enterprise_grid_poster(slack_user):
    """Enterprise Grid member ids start with W, not U — still enrich the charge."""
    charge = _mt_charge()
    slack_user.user_id = str(uuid4())
    slack_user.ray_user_group_id = "billing-group-1"
    success_data = {
        "task_uuid": str(uuid4()),
        "file_id": "file-1",
        "tokens": 0,
        "client_id": slack_user.ray_client_id,
        "target_language": "et",
        "channel_id": "D123",
        "mt_charge": charge,
        "team_id": "T123",
        "slack_user_id": "W0123ABCD",
    }
    fake_slack = MagicMock()
    fake_slack.users_info = AsyncMock(
        return_value={
            "user": {
                "profile": {
                    "email": "grid.poster@example.com",
                    "real_name": "Grid Poster",
                }
            }
        }
    )
    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.saq_jobs.tasks.update_slack_job", new=AsyncMock()),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/foo", "file_name": "out.docx"}),
        ),
        patch("app.saq_jobs.tasks.delete_from_file_server", new=AsyncMock()),
        patch(
            "app.slack.listener_actions.get_language_name",
            new=AsyncMock(return_value="Estonian"),
        ),
        patch("app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()),
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch(
            "app.saq_jobs.dispatch.enqueue_document_mt_charge", new=AsyncMock()
        ) as mock_enqueue,
    ):
        result = await slack_upload_mt_result(_ctx(), success_data=success_data)

    assert result["status"] == "delivered"
    fake_slack.users_info.assert_awaited_once_with(user="W0123ABCD")
    assert (
        mock_enqueue.await_args.kwargs["charge"]["email"] == "grid.poster@example.com"
    )
    assert mock_enqueue.await_args.kwargs["charge"]["client_name"] == "Grid Poster"


@pytest.mark.asyncio
async def test_slack_upload_mt_result_skips_profile_lookup_for_org_uuid(slack_user):
    """Org uuid is not a Slack user id — skip users_info and still charge."""
    charge = _mt_charge()
    org_uuid = str(uuid4())
    slack_user.user_id = org_uuid
    slack_user.ray_user_group_id = "billing-group-1"
    success_data = {
        "task_uuid": str(uuid4()),
        "file_id": "file-1",
        "tokens": 0,
        "client_id": slack_user.ray_client_id,
        "target_language": "et",
        "channel_id": "D123",
        "mt_charge": charge,
    }
    fake_slack = MagicMock()
    fake_slack.users_info = AsyncMock()
    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.saq_jobs.tasks.update_slack_job", new=AsyncMock()),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/foo", "file_name": "out.docx"}),
        ),
        patch("app.saq_jobs.tasks.delete_from_file_server", new=AsyncMock()),
        patch(
            "app.slack.listener_actions.get_language_name",
            new=AsyncMock(return_value="Estonian"),
        ),
        patch("app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()),
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch(
            "app.saq_jobs.dispatch.enqueue_document_mt_charge", new=AsyncMock()
        ) as mock_enqueue,
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        result = await slack_upload_mt_result(_ctx(), success_data=success_data)

    assert result["status"] == "delivered"
    fake_slack.users_info.assert_not_awaited()
    mock_notify.assert_not_called()
    mock_enqueue.assert_awaited_once()
    assert "email" not in mock_enqueue.await_args.kwargs["charge"]


@pytest.mark.asyncio
async def test_charge_document_mt_happy_path():
    """Relays the prepared charge to the gateway and surfaces both txn uuids."""
    charge = _mt_charge()
    relay = AsyncMock(
        return_value={"transaction_uuid": "txn-1", "pdf_transaction_uuid": "txn-pdf"}
    )
    link_job = AsyncMock()
    with (
        patch("app.auth.connector.log_document_mt_by_client_id", new=relay),
        patch(
            "app.saq_jobs.tasks.update_slack_job_transaction_uuid",
            new=link_job,
        ),
    ):
        result = await charge_document_mt(
            _ctx(), client_id="client-1", charge=charge, task_uuid="task-1"
        )

    assert result == {
        "status": "charged",
        "transaction_uuid": "txn-1",
        "pdf_transaction_uuid": "txn-pdf",
    }
    relay.assert_awaited_once_with("client-1", charge)
    # RAY-80941: document-MT txn is written onto slack_job for report linking.
    link_job.assert_awaited_once_with("task-1", "txn-1")


@pytest.mark.asyncio
async def test_charge_document_mt_retries_on_connect_timeout():
    """A transient ConnectTimeout is retried inside the task."""
    relay = AsyncMock(
        side_effect=[httpx.ConnectTimeout("boom"), {"transaction_uuid": "txn-2"}]
    )
    with (
        patch("app.auth.connector.log_document_mt_by_client_id", new=relay),
        patch("app.api.http_client.asyncio.sleep", new=AsyncMock()),
    ):
        result = await charge_document_mt(
            _ctx(), client_id="c", charge={"idempotency_key": "k"}, task_uuid="t"
        )

    assert result["transaction_uuid"] == "txn-2"
    assert relay.await_count == 2


@pytest.mark.asyncio
async def test_charge_document_mt_notifies_on_final_attempt():
    """On the final, non-retryable attempt the failure raises a BugLog alert."""
    relay = AsyncMock(side_effect=RuntimeError("gateway 500"))
    with (
        patch("app.auth.connector.log_document_mt_by_client_id", new=relay),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        with pytest.raises(RuntimeError):
            await charge_document_mt(
                _ctx(retryable=False),
                client_id="c",
                charge={"idempotency_key": "key-doc"},
                task_uuid="t",
            )

    mock_notify.assert_called_once()
    assert mock_notify.call_args.kwargs["extra"]["idempotency_key"] == "key-doc"


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    """Build an httpx.HTTPStatusError for a given gateway status code."""
    request = httpx.Request("POST", "https://gateway/mt/transaction")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("rejected", request=request, response=response)


@pytest.mark.asyncio
async def test_charge_document_mt_alerts_immediately_on_client_error():
    """A 4xx gateway rejection alerts on the first attempt (retry cannot fix it)."""
    relay = AsyncMock(side_effect=_http_status_error(422))
    with (
        patch("app.auth.connector.log_document_mt_by_client_id", new=relay),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await charge_document_mt(
                _ctx(attempts=1, retryable=True),
                client_id="c",
                charge={"idempotency_key": "key-doc"},
                task_uuid="t",
            )

    mock_notify.assert_called_once()
    assert mock_notify.call_args.kwargs["extra"]["status_code"] == 422


@pytest.mark.asyncio
async def test_charge_document_mt_silent_on_retryable_transient_error():
    """A non-4xx error on a retryable, non-final attempt does not alert (no spam)."""
    relay = AsyncMock(side_effect=RuntimeError("gateway 500"))
    with (
        patch("app.auth.connector.log_document_mt_by_client_id", new=relay),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        with pytest.raises(RuntimeError):
            await charge_document_mt(
                _ctx(attempts=1, retryable=True),
                client_id="c",
                charge={"idempotency_key": "key-doc"},
                task_uuid="t",
            )

    mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_charge_document_mt_warns_on_first_attempt_replay(caplog):
    """A first-attempt replay (no new debit) is logged rather than reported clean."""
    import logging

    relay = AsyncMock(return_value={"transaction_uuid": "txn-1", "replayed": True})
    with (
        patch("app.auth.connector.log_document_mt_by_client_id", new=relay),
        caplog.at_level(logging.WARNING),
    ):
        result = await charge_document_mt(
            _ctx(attempts=1, retryable=True),
            client_id="c",
            charge={"idempotency_key": "key-doc"},
            task_uuid="t",
        )

    assert result["status"] == "charged"
    assert any("replayed on first attempt" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_charge_inline_mt_usage_alerts_immediately_on_client_error():
    """Inline billing also alerts immediately on a 4xx gateway rejection."""
    relay = AsyncMock(side_effect=_http_status_error(403))
    with (
        patch("app.auth.connector.log_inline_mt_usage_by_client_id", new=relay),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await charge_inline_mt_usage(
                _ctx(attempts=1, retryable=True),
                billing={"idempotency_key": "key-inline", "client_id": "c"},
                usage_log={},
            )

    mock_notify.assert_called_once()
    assert mock_notify.call_args.kwargs["extra"]["status_code"] == 403
