"""Tests for evaluation quote Redis session helpers."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.constants import HUMAN_EVALUATION_WORKFLOW_UUID
from app.slack.evaluation_quotes import (
    get_evaluate_quote_session,
    job_is_human_translation_quote,
    save_evaluate_quote_session,
    update_evaluate_quote_session,
    update_evaluate_quote_stage,
)
from app.slack.pdf_evaluate_quotes import (
    get_pdf_evaluate_quote_session,
    save_pdf_evaluate_quote_session,
)


@pytest.mark.asyncio
async def test_save_and_get_evaluate_quote_session():
    stored = {}

    async def fake_set(key, value, ex=None):
        stored[key] = value

    async def fake_get(key):
        return stored.get(key)

    with patch("app.slack.evaluation_quotes.redis_conn") as mock_redis:
        mock_redis.set = AsyncMock(side_effect=fake_set)
        mock_redis.get = AsyncMock(side_effect=fake_get)

        await save_evaluate_quote_session(
            "job-1",
            channel_id="C123",
            user_id="U123",
            team_id="T123",
            stage="awaiting_ai",
            pdf_page_count=4,
            quote_snapshot={"token_cost": 100},
        )

        session = await get_evaluate_quote_session("job-1")
        assert session is not None
        assert session["stage"] == "awaiting_ai"
        assert session["pdf_page_count"] == 4
        assert session["quote_snapshot"]["token_cost"] == 100


@pytest.mark.asyncio
async def test_update_evaluate_quote_stage():
    stored = {
        "slack-ray-translator:evaluate-quote:job-2": json.dumps(
            {
                "channel_id": "C1",
                "user_id": "U1",
                "team_id": "T1",
                "stage": "awaiting_ai",
            }
        )
    }

    async def fake_get(key):
        return stored.get(key)

    async def fake_set(key, value, ex=None):
        stored[key] = value

    with patch("app.slack.evaluation_quotes.redis_conn") as mock_redis:
        mock_redis.get = AsyncMock(side_effect=fake_get)
        mock_redis.set = AsyncMock(side_effect=fake_set)

        await update_evaluate_quote_stage("job-2", "awaiting_qe")
        session = await get_evaluate_quote_session("job-2")
        assert session is not None
        assert session["stage"] == "awaiting_qe"


@pytest.mark.asyncio
async def test_save_evaluate_quote_session_preserves_distinct_ht_message_ts():
    stored = {
        "slack-ray-translator:evaluate-quote:job-preserve": json.dumps(
            {
                "channel_id": "C1",
                "user_id": "U1",
                "team_id": "T1",
                "stage": "awaiting_qe",
                "message_ts": "333.444",
                "ai_message_ts": "111.222",
                "quote_snapshot": {"auto_submit_human_job": True},
            }
        )
    }

    async def fake_get(key):
        return stored.get(key)

    async def fake_set(key, value, ex=None):
        stored[key] = value

    with patch("app.slack.evaluation_quotes.redis_conn") as mock_redis:
        mock_redis.get = AsyncMock(side_effect=fake_get)
        mock_redis.set = AsyncMock(side_effect=fake_set)

        await save_evaluate_quote_session(
            "job-preserve",
            channel_id="C1",
            user_id="U1",
            team_id="T1",
            stage="processing_qe",
            quote_snapshot={"auto_submit_human_job": True, "token_cost": 6},
            message_ts=None,
            ai_message_ts=None,
        )

        session = await get_evaluate_quote_session("job-preserve")
        assert session is not None
        assert session["message_ts"] == "333.444"
        assert session["ai_message_ts"] == "111.222"
        assert session["stage"] == "processing_qe"


@pytest.mark.asyncio
async def test_update_evaluate_quote_session_preserves_existing_fields():
    stored = {
        "slack-ray-translator:evaluate-quote:job-3": json.dumps(
            {
                "channel_id": "C1",
                "stage": "awaiting_ai",
                "quote_snapshot": {"token_cost": 100},
            }
        )
    }

    async def fake_get(key):
        return stored.get(key)

    async def fake_set(key, value, ex=None):
        stored[key] = value

    with patch("app.slack.evaluation_quotes.redis_conn") as mock_redis:
        mock_redis.get = AsyncMock(side_effect=fake_get)
        mock_redis.set = AsyncMock(side_effect=fake_set)

        await update_evaluate_quote_session(
            "job-3",
            {
                "quote_snapshot": {
                    "token_cost": 25,
                    "ai_translation_file_and_languages": ["file-1:lang-1"],
                }
            },
        )

        session = await get_evaluate_quote_session("job-3")
        assert session is not None
        assert session["channel_id"] == "C1"
        assert session["stage"] == "awaiting_ai"
        assert session["quote_snapshot"]["token_cost"] == 25


class TestJobIsHumanTranslationQuote:
    """Staged Slack quotes clear HUMAN_EVALUATION, so HT must be detectable without it."""

    @pytest.mark.asyncio
    async def test_legacy_human_evaluation_workflow(self):
        assert await job_is_human_translation_quote(
            {"uuid": "job-1", "workflow_uuid": HUMAN_EVALUATION_WORKFLOW_UUID}
        )

    @pytest.mark.asyncio
    async def test_ht_quote_after_qe_flag(self):
        for flag in (True, "true"):
            assert await job_is_human_translation_quote(
                {
                    "uuid": "job-1",
                    "workflow_uuid": None,
                    "extra_info": {"slack_ht_quote_after_qe": flag},
                }
            )

    @pytest.mark.asyncio
    async def test_combined_qe_human_quote_session(self):
        """Staged admin HT: no workflow and no flag — only the quote session knows."""
        stored = {
            "slack-ray-translator:evaluate-quote:job-staged": json.dumps(
                {"quote_snapshot": {"auto_submit_human_job": True}}
            )
        }

        async def fake_get(key):
            return stored.get(key)

        with patch("app.slack.evaluation_quotes.redis_conn") as mock_redis:
            mock_redis.get = AsyncMock(side_effect=fake_get)

            assert await job_is_human_translation_quote(
                {"uuid": "job-staged", "workflow_uuid": None, "extra_info": {}}
            )

    @pytest.mark.asyncio
    async def test_quality_evaluation_only_job(self):
        stored = {
            "slack-ray-translator:evaluate-quote:job-qe": json.dumps(
                {"quote_snapshot": {"service": "quality_evaluation"}}
            )
        }

        async def fake_get(key):
            return stored.get(key)

        with patch("app.slack.evaluation_quotes.redis_conn") as mock_redis:
            mock_redis.get = AsyncMock(side_effect=fake_get)

            assert not await job_is_human_translation_quote(
                {"uuid": "job-qe", "workflow_uuid": "other-workflow", "extra_info": {}}
            )


@pytest.mark.asyncio
async def test_pdf_quote_session_initially_selects_all_files_and_languages():
    stored = {}

    async def fake_set(key, value, ex=None):
        stored[key] = value

    async def fake_get(key):
        return stored.get(key)

    with patch("app.slack.pdf_evaluate_quotes.redis_conn") as mock_redis:
        mock_redis.set = AsyncMock(side_effect=fake_set)
        mock_redis.get = AsyncMock(side_effect=fake_get)

        quote_id = await save_pdf_evaluate_quote_session(
            channel_id="C1",
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            files=[
                {
                    "id": "file-1",
                    "title": "source.pdf",
                    "pdf_page_count": 3,
                }
            ],
            target_langs_uuid=["lang-1"],
            reference="reference",
            source_lang_uuid="source-lang",
            workflow_uuid=None,
            job_notes="",
            ai_token_estimate=20,
            pdf_page_count=3,
        )
        session = await get_pdf_evaluate_quote_session(quote_id)

    assert session is not None
    assert session["selected_file_ids"] == ["file-1"]
    assert session["selected_target_langs_uuid"] == ["lang-1"]
