"""Tests for evaluate quote accept handlers."""

from collections.abc import Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.api.verify import VerifyAPIError
from app.slack.evaluation_quotes import (
    STAGE_ACCEPTED_AI,
    STAGE_AWAITING_AI,
    STAGE_PROCESSING_AI,
)
from app.slack.listeners import (
    evaluation_ai_quote_accept_action,
    evaluation_ai_quote_adjust_action,
    evaluation_ai_quote_adjust_submit,
    evaluation_qe_human_quote_accept_action,
)

QuoteAcceptHandler = Callable[..., Awaitable[None]]
AiQuoteAcceptAction = cast(QuoteAcceptHandler, evaluation_ai_quote_accept_action)
QeHumanQuoteAcceptAction = cast(
    QuoteAcceptHandler, evaluation_qe_human_quote_accept_action
)
AiQuoteAdjustAction = cast(QuoteAcceptHandler, evaluation_ai_quote_adjust_action)
AiQuoteAdjustSubmit = cast(QuoteAcceptHandler, evaluation_ai_quote_adjust_submit)


def _mock_redis(*, lock_acquired: bool = True, session: dict | None = None):
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=lock_acquired)
    mock_redis.delete = AsyncMock()
    return mock_redis


@pytest.fixture(autouse=True)
def connected_ray_client():
    """Accept handlers gate on a connected member; these contexts are mocks."""
    with patch(
        "app.slack.evaluation_quote_actions.require_ray_client",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_require:
        yield mock_require


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

    with patch("app.slack.evaluation_quote_actions.redis_conn", _mock_redis()):
        with patch(
            "app.slack.evaluation_quote_actions.get_evaluate_quote_session",
            new_callable=AsyncMock,
            return_value={
                "stage": STAGE_AWAITING_AI,
                "quote_snapshot": {
                    "ai_translation_file_and_languages": ["file-1:lang-1"]
                },
            },
        ):
            with patch(
                "app.slack.evaluation_quote_actions.update_evaluate_quote_stage",
                new_callable=AsyncMock,
            ):
                with patch(
                    "app.slack.evaluation_quote_actions.update_evaluate_quote_slack_message",
                    new_callable=AsyncMock,
                ) as mock_update:
                    with patch(
                        "app.slack.evaluation_quote_actions.get_evaluation_job_quote",
                        new_callable=AsyncMock,
                        return_value={
                            "token": 50,
                            "services_costs": {"ai_translation": 50},
                        },
                    ):
                        with patch(
                            "app.slack.evaluation_quote_actions.get_client_evaluation_job",
                            new_callable=AsyncMock,
                            return_value=mock_job,
                        ):
                            with patch(
                                "app.slack.evaluation_quote_actions.proceed_evaluation_job",
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
    assert mock_proceed.await_args.kwargs["ai_translation_file_and_languages"] == [
        "file-1:lang-1"
    ]
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

    with patch("app.slack.evaluation_quote_actions.redis_conn", _mock_redis()):
        with patch(
            "app.slack.evaluation_quote_actions.get_evaluate_quote_session",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch(
                "app.slack.evaluation_quote_actions.update_evaluate_quote_stage",
                new_callable=AsyncMock,
            ):
                with patch(
                    "app.slack.evaluation_quote_actions.get_evaluation_job_quote",
                    new_callable=AsyncMock,
                    return_value={
                        "token": 80,
                        "services_costs": {"quality_evaluation": 80},
                    },
                ):
                    with patch(
                        "app.slack.evaluation_quote_actions.get_client_evaluation_job",
                        new_callable=AsyncMock,
                        return_value=job,
                    ):
                        with patch(
                            "app.slack.evaluation_quote_actions.get_job_pricing",
                            new_callable=AsyncMock,
                            return_value={"data": []},
                        ) as mock_pricing:
                            with patch(
                                "app.slack.evaluation_quote_actions.proceed_quality_evaluation",
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
        quality_evaluation_file_and_languages=["file-1:lang-1"],
    )
    final_update = client.chat_update.await_args_list[-1].kwargs
    assert final_update["channel"] == "C1"
    assert "calculating your final discount based on AI quality" in str(
        final_update["blocks"]
    )


@pytest.mark.asyncio
async def test_evaluation_ai_quote_accept_insufficient_balance_updates_message():
    job_uuid = str(uuid4())
    context = {"channel_id": "C1", "ray": MagicMock(client=MagicMock())}
    client = AsyncMock()

    with patch("app.slack.evaluation_quote_actions.redis_conn", _mock_redis()):
        with patch(
            "app.slack.evaluation_quote_actions.get_evaluate_quote_session",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch(
                "app.slack.evaluation_quote_actions.update_evaluate_quote_stage",
                new_callable=AsyncMock,
            ):
                with patch(
                    "app.slack.evaluation_quote_actions.update_evaluate_quote_slack_message",
                    new_callable=AsyncMock,
                ) as mock_update:
                    with patch(
                        "app.slack.evaluation_quote_actions.get_evaluation_job_quote",
                        new_callable=AsyncMock,
                        return_value={
                            "token": 50,
                            "services_costs": {"ai_translation": 50},
                        },
                    ):
                        with patch(
                            "app.slack.evaluation_quote_actions.get_client_evaluation_job",
                            new_callable=AsyncMock,
                            return_value={"data": {"extra_info": {}}},
                        ):
                            with patch(
                                "app.slack.evaluation_quote_actions.proceed_evaluation_job",
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

    with patch("app.slack.evaluation_quote_actions.redis_conn", _mock_redis()):
        with patch(
            "app.slack.evaluation_quote_actions.get_evaluate_quote_session",
            new_callable=AsyncMock,
            return_value={"stage": STAGE_ACCEPTED_AI},
        ):
            with patch(
                "app.slack.evaluation_quote_actions.proceed_evaluation_job",
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
        "app.slack.evaluation_quote_actions.redis_conn",
        _mock_redis(lock_acquired=False),
    ):
        with patch(
            "app.slack.evaluation_quote_actions.get_evaluate_quote_session",
            new_callable=AsyncMock,
            return_value={
                "stage": "awaiting_ai",
                "quote_snapshot": {"token_cost": 50, "service_label": "AI Translation"},
            },
        ):
            with patch(
                "app.slack.evaluation_quote_actions.update_evaluate_quote_slack_message",
                new_callable=AsyncMock,
            ) as mock_update:
                with patch(
                    "app.slack.evaluation_quote_actions.proceed_evaluation_job",
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


@pytest.mark.asyncio
async def test_evaluation_ai_quote_accept_timeout_does_not_rearm_accept():
    """An ambiguous proceed() failure may already have debited — keep Accept off."""
    job_uuid = str(uuid4())
    context = {"channel_id": "C1", "ray": MagicMock(client=MagicMock())}
    client = AsyncMock()
    redis_stub = _mock_redis()

    with (
        patch("app.slack.evaluation_quote_actions.redis_conn", redis_stub),
        patch(
            "app.slack.evaluation_quote_actions.get_evaluate_quote_session",
            new_callable=AsyncMock,
            return_value={
                "stage": STAGE_AWAITING_AI,
                "quote_snapshot": {
                    "ai_translation_file_and_languages": ["file-1:lang-1"]
                },
            },
        ),
        patch(
            "app.slack.evaluation_quote_actions.update_evaluate_quote_stage",
            new_callable=AsyncMock,
        ) as mock_stage,
        patch(
            "app.slack.evaluation_quote_actions.update_evaluate_quote_slack_message",
            new_callable=AsyncMock,
        ) as mock_update,
        patch(
            "app.slack.evaluation_quote_actions.get_evaluation_job_quote",
            new_callable=AsyncMock,
            return_value={"token": 50, "services_costs": {"ai_translation": 50}},
        ),
        patch(
            "app.slack.evaluation_quote_actions.get_client_evaluation_job",
            new_callable=AsyncMock,
            return_value={"data": {"uuid": job_uuid, "extra_info": {}}},
        ),
        patch(
            "app.slack.evaluation_quote_actions.proceed_evaluation_job",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("verify timed out"),
        ),
        patch("app.slack.evaluation_quote_actions.notify_exception"),
    ):
        await AiQuoteAcceptAction(
            ack=AsyncMock(),
            client=client,
            body={"user": {"id": "U1"}, "message": {"ts": "123.456"}},
            action={"value": job_uuid},
            context=context,
        )

    final_update = mock_update.await_args_list[-1].kwargs
    assert final_update["actions"] is False
    assert "do not accept this quote again" in final_update["status_message"]
    # Stage stays at processing_ai so a second click hits the terminal-stage guard.
    assert [call.args[1] for call in mock_stage.await_args_list] == [
        STAGE_PROCESSING_AI
    ]
    redis_stub.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_evaluation_ai_quote_accept_rejects_other_user():
    """The Accept button is visible to the channel; only the owner may spend."""
    job_uuid = str(uuid4())
    context = {"channel_id": "C1", "user_id": "U_OTHER", "ray": MagicMock()}
    client = AsyncMock()

    with (
        patch("app.slack.evaluation_quote_actions.redis_conn", _mock_redis()),
        patch(
            "app.slack.evaluation_quote_actions.get_evaluate_quote_session",
            new_callable=AsyncMock,
            return_value={"stage": STAGE_AWAITING_AI, "user_id": "U_OWNER"},
        ),
        patch(
            "app.slack.evaluation_quote_actions.proceed_evaluation_job",
            new_callable=AsyncMock,
        ) as mock_proceed,
    ):
        await AiQuoteAcceptAction(
            ack=AsyncMock(),
            client=client,
            body={"user": {"id": "U_OTHER"}, "message": {"ts": "123.456"}},
            action={"value": job_uuid},
            context=context,
        )

    mock_proceed.assert_not_called()
    client.chat_postMessage.assert_awaited_once()
    assert "permission" in client.chat_postMessage.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_evaluation_qe_human_quote_accept_rejects_other_user():
    """Same ownership rule for the combined QE + Human Translation quote."""
    job_uuid = str(uuid4())
    context = {"channel_id": "C1", "user_id": "U_OTHER", "ray": MagicMock()}
    client = AsyncMock()

    with (
        patch("app.slack.evaluation_quote_actions.redis_conn", _mock_redis()),
        patch(
            "app.slack.evaluation_quote_actions.get_evaluate_quote_session",
            new_callable=AsyncMock,
            return_value={"stage": "awaiting_qe", "user_id": "U_OWNER"},
        ),
        patch(
            "app.slack.evaluation_quote_actions.proceed_quality_evaluation",
            new_callable=AsyncMock,
        ) as mock_proceed,
    ):
        await QeHumanQuoteAcceptAction(
            ack=AsyncMock(),
            client=client,
            body={"user": {"id": "U_OTHER"}, "message": {"ts": "123.456"}},
            action={"value": job_uuid},
            context=context,
        )

    mock_proceed.assert_not_called()
    client.chat_postMessage.assert_awaited_once()
    assert "permission" in client.chat_postMessage.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_ai_quote_adjust_opens_loading_modal_before_service_calls():
    calls: list[str] = []
    ack = AsyncMock(side_effect=lambda: calls.append("ack"))

    async def open_loading(*args, **kwargs):
        calls.append("open")
        return "view-1"

    async def populate(*args, **kwargs):
        calls.append("populate")

    with (
        patch(
            "app.slack.handlers.evaluate.open_loading_modal",
            new=AsyncMock(side_effect=open_loading),
        ),
        patch(
            "app.slack.handlers.evaluate.populate_ai_quote_adjustment_modal",
            new=AsyncMock(side_effect=populate),
        ),
    ):
        await AiQuoteAdjustAction(
            ack=ack,
            client=AsyncMock(),
            body={"trigger_id": "trigger-1", "user": {"id": "U1"}},
            action={
                "value": "job-1",
                "action_id": "evaluation_ai_quote_adjust",
            },
            context={},
        )

    assert calls == ["ack", "open", "populate"]


@pytest.mark.asyncio
async def test_ai_quote_adjust_submit_rejects_empty_selection():
    ack = AsyncMock()
    view = {
        "state": {
            "values": {
                "ai_quote_language_file-1_lang-1": {
                    "evaluation_ai_quote_language_selection": {
                        "selected_options": [],
                    }
                },
            }
        },
        "private_metadata": '{"quote_id":"job-1","quote_kind":"evaluate"}',
        "blocks": [],
    }

    with patch(
        "app.slack.handlers.evaluate.persist_ai_quote_adjustment",
        new_callable=AsyncMock,
    ) as mock_persist:
        await AiQuoteAdjustSubmit(
            ack=ack,
            body={"view": view, "user": {"id": "U1"}},
            client=AsyncMock(),
            context={},
        )

    assert ack.await_args.kwargs["response_action"] == "update"
    assert "Select at least one file and language" in str(ack.await_args.kwargs["view"])
    mock_persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_quote_adjust_submit_persists_and_accepts_quote():
    ack = AsyncMock()
    client = AsyncMock()
    context = {"channel_id": "C1"}
    view = {
        "state": {
            "values": {
                "ai_quote_language_file-1_lang-1": {
                    "evaluation_ai_quote_language_selection": {
                        "selected_options": [{"value": "file-1:lang-1"}],
                    }
                },
            }
        },
        "private_metadata": (
            '{"quote_id":"job-1","quote_kind":"evaluate",'
            '"channel_id":"C1","message_ts":"111.222"}'
        ),
    }

    body = {"view": view, "user": {"id": "U1"}}
    with (
        patch(
            "app.slack.handlers.evaluate.persist_ai_quote_adjustment",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_persist,
        patch(
            "app.slack.handlers.evaluate.accept_ai_translation_quote",
            new_callable=AsyncMock,
        ) as mock_accept,
    ):
        await AiQuoteAdjustSubmit(
            ack=ack,
            body=body,
            client=client,
            context=context,
        )

    ack.assert_awaited_once_with(response_action="clear")
    mock_persist.assert_awaited_once_with(
        client,
        quote_id="job-1",
        quote_kind="evaluate",
        selected_pairs=["file-1:lang-1"],
        user_id="U1",
        context=context,
        channel_id="C1",
        message_ts="111.222",
    )
    mock_accept.assert_awaited_once_with(
        client=client,
        body=body,
        action={"value": "job-1"},
        context=context,
        selected_pairs_override=["file-1:lang-1"],
    )
