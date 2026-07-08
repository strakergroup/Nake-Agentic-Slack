"""Tests for evaluation quote Redis session helpers."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.slack.evaluation_quotes import (
    get_evaluate_quote_session,
    save_evaluate_quote_session,
    update_evaluate_quote_stage,
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
