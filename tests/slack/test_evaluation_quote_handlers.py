"""Tests for evaluate quote accept handlers."""

from collections.abc import Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.api.verify import VerifyAPIError
from app.slack.evaluation_quotes import STAGE_ACCEPTED_AI
from app.slack.listeners import (
    evaluation_ai_quote_accept_action,
    evaluation_qe_human_quote_accept_action,
)

QuoteAcceptHandler = Callable[..., Awaitable[None]]
AiQuoteAcceptAction = cast(QuoteAcceptHandler, evaluation_ai_quote_accept_action)
QeHumanQuoteAcceptAction = cast(
    QuoteAcceptHandler, evaluation_qe_human_quote_accept_action
)


def _mock_redis(*, lock_acquired: bool = True, session: dict | None = None):
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=lock_acquired)
    mock_redis.delete = AsyncMock()
    return mock_redis


@pytest.mark.asyncio
async def test_evaluation_ai_quote_accept_calls_proceed_with_pdf_tokens():
    job_uuid = str(uuid4())
    body = {"user": {"id": "U1"}, "message": {"ts": "123.456"}}
    action = {"value": job_uuid}
    context = {"channel_id": "C1", "ray": MagicMock(client=MagicMock())}
    client = AsyncMock()

    mock_job = {
        "data": {
            "uuid": job_uuid,
            "extra_info": {"pdf_page_count": 2},
        }
    }

    with patch("app.slack.listeners.redis_conn", _mock_redis()):
        with patch(
            "app.slack.listeners.get_evaluate_quote_session",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch(
                "app.slack.listeners.update_evaluate_quote_stage",
                new_callable=AsyncMock,
            ):
                with patch(
                    "app.slack.listeners.update_evaluate_quote_slack_message",
                    new_callable=AsyncMock,
                ) as mock_update:
                    with patch(
                        "app.slack.listeners.get_evaluation_job_quote",
                        new_callable=AsyncMock,
                        return_value={
                            "token": 50,
                            "services_costs": {"ai_translation": 50},
                        },
                    ):
                        with patch(
                            "app.slack.listeners.get_client_evaluation_job",
                            new_callable=AsyncMock,
                            return_value=mock_job,
                        ):
                            with patch(
                                "app.slack.listeners.proceed_evaluation_job",
                                new_callable=AsyncMock,
                            ) as mock_proceed:
                                await AiQuoteAcceptAction(
                                    ack=AsyncMock(),
                                    client=client,
                                    body=body,
                                    action=action,
                                    context=context,
                                )

    mock_proceed.assert_awaited_once()
    assert mock_proceed.await_args is not None
    assert mock_proceed.await_args.kwargs["skip_quality_evaluation"] is True
    assert mock_proceed.await_args.kwargs["token_cost"] == 100
    assert mock_update.await_count >= 2
    final_update = mock_update.await_args_list[-1].kwargs
    assert final_update["actions"] is False
    assert "accepted" in final_update["status_message"].lower()
    client.chat_postMessage.assert_not_called()


@pytest.mark.asyncio
async def test_evaluation_qe_human_quote_accept_runs_quality_evaluation():
    job_uuid = str(uuid4())
    body = {"user": {"id": "U1"}, "message": {"ts": "123.456"}}
    action = {"value": job_uuid}
    context = {"channel_id": "C1", "ray": MagicMock(client=MagicMock())}
    client = AsyncMock()
    job = {
        "data": {
            "uuid": job_uuid,
            "workflow_uuid": "workflow-123",
            "source_files": [
                {
                    "file_uuid": "file-1",
                    "filename": "test.txt",
                    "target_files": [{"language_uuid": "lang-1"}],
                }
            ],
            "target_languages": [{"uuid": "lang-1", "name": "French"}],
        }
    }

    with patch("app.slack.listeners.redis_conn", _mock_redis()):
        with patch(
            "app.slack.listeners.get_evaluate_quote_session",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch(
                "app.slack.listeners.update_evaluate_quote_stage",
                new_callable=AsyncMock,
            ):
                with patch(
                    "app.slack.listeners.get_evaluation_job_quote",
                    new_callable=AsyncMock,
                    return_value={
                        "token": 80,
                        "services_costs": {"quality_evaluation": 80},
                    },
                ):
                    with patch(
                        "app.slack.listeners.get_client_evaluation_job",
                        new_callable=AsyncMock,
                        return_value=job,
                    ):
                        with patch(
                            "app.slack.listeners.get_job_pricing",
                            new_callable=AsyncMock,
                            return_value={"data": []},
                        ) as mock_pricing:
                            with patch(
                                "app.slack.listeners.proceed_quality_evaluation",
                                new_callable=AsyncMock,
                            ) as mock_proceed_qe:
                                await QeHumanQuoteAcceptAction(
                                    ack=AsyncMock(),
                                    client=client,
                                    body=body,
                                    action=action,
                                    context=context,
                                )

    mock_pricing.assert_awaited_once()
    assert mock_pricing.await_args.kwargs["assumed_quality_tier"] == "bad"
    mock_proceed_qe.assert_awaited_once_with(
        context["ray"].client,
        job_uuid,
        token_cost=80,
        human_translation_file_and_languages=["file-1:lang-1"],
    )
    final_update = client.chat_update.await_args_list[-1].kwargs
    assert final_update["channel"] == "C1"
    assert "calculating your final discount with Arbitr" in str(final_update["blocks"])


@pytest.mark.asyncio
async def test_evaluation_ai_quote_accept_insufficient_balance_updates_message():
    job_uuid = str(uuid4())
    context = {"channel_id": "C1", "ray": MagicMock(client=MagicMock())}
    client = AsyncMock()

    with patch("app.slack.listeners.redis_conn", _mock_redis()):
        with patch(
            "app.slack.listeners.get_evaluate_quote_session",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch(
                "app.slack.listeners.update_evaluate_quote_stage",
                new_callable=AsyncMock,
            ):
                with patch(
                    "app.slack.listeners.update_evaluate_quote_slack_message",
                    new_callable=AsyncMock,
                ) as mock_update:
                    with patch(
                        "app.slack.listeners.get_evaluation_job_quote",
                        new_callable=AsyncMock,
                        return_value={
                            "token": 50,
                            "services_costs": {"ai_translation": 50},
                        },
                    ):
                        with patch(
                            "app.slack.listeners.get_client_evaluation_job",
                            new_callable=AsyncMock,
                            return_value={"data": {"extra_info": {}}},
                        ):
                            with patch(
                                "app.slack.listeners.proceed_evaluation_job",
                                new_callable=AsyncMock,
                                side_effect=VerifyAPIError("Insufficient", 402),
                            ):
                                await AiQuoteAcceptAction(
                                    ack=AsyncMock(),
                                    client=client,
                                    body={
                                        "user": {"id": "U1"},
                                        "message": {"ts": "123.456"},
                                    },
                                    action={"value": job_uuid},
                                    context=context,
                                )

    assert mock_update.await_args is not None
    assert "Insufficient" in mock_update.await_args.kwargs["status_message"]
    assert mock_update.await_args.kwargs["actions"] is False
    client.chat_postMessage.assert_not_called()


@pytest.mark.asyncio
async def test_evaluation_ai_quote_accept_ignores_duplicate_clicks():
    job_uuid = str(uuid4())
    context = {"channel_id": "C1", "ray": MagicMock(client=MagicMock())}
    client = AsyncMock()

    with patch("app.slack.listeners.redis_conn", _mock_redis()):
        with patch(
            "app.slack.listeners.get_evaluate_quote_session",
            new_callable=AsyncMock,
            return_value={"stage": STAGE_ACCEPTED_AI},
        ):
            with patch(
                "app.slack.listeners.proceed_evaluation_job",
                new_callable=AsyncMock,
            ) as mock_proceed:
                await AiQuoteAcceptAction(
                    ack=AsyncMock(),
                    client=client,
                    body={"user": {"id": "U1"}, "message": {"ts": "123.456"}},
                    action={"value": job_uuid},
                    context=context,
                )

    mock_proceed.assert_not_called()
    client.chat_update.assert_not_called()
    client.chat_postMessage.assert_not_called()


@pytest.mark.asyncio
async def test_evaluation_ai_quote_accept_in_progress_lock_updates_message():
    job_uuid = str(uuid4())
    context = {"channel_id": "C1", "ray": MagicMock(client=MagicMock())}
    client = AsyncMock()

    with patch(
        "app.slack.listeners.redis_conn",
        _mock_redis(lock_acquired=False),
    ):
        with patch(
            "app.slack.listeners.get_evaluate_quote_session",
            new_callable=AsyncMock,
            return_value={
                "stage": "awaiting_ai",
                "quote_snapshot": {"token_cost": 50, "service_label": "AI Translation"},
            },
        ):
            with patch(
                "app.slack.listeners.update_evaluate_quote_slack_message",
                new_callable=AsyncMock,
            ) as mock_update:
                with patch(
                    "app.slack.listeners.proceed_evaluation_job",
                    new_callable=AsyncMock,
                ) as mock_proceed:
                    await AiQuoteAcceptAction(
                        ack=AsyncMock(),
                        client=client,
                        body={"user": {"id": "U1"}, "message": {"ts": "123.456"}},
                        action={"value": job_uuid},
                        context=context,
                    )

    mock_proceed.assert_not_called()
    mock_update.assert_awaited_once()
    assert mock_update.await_args.kwargs["actions"] is False
    client.chat_postMessage.assert_not_called()
