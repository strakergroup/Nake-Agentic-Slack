"""Tests for RAY-79115 sequential evaluate quote ray events."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.auth.connector import SlackUser
from app.constants import HUMAN_VERIFICATION_WORKFLOW_UUID
from app.dependencies import RayEvent, RayEventAuth


@pytest.fixture
def user_id():
    return "U123"


@pytest.fixture
def team_id():
    return "T123"


@pytest.fixture
def mock_slack_user(user_id, team_id):
    return SlackUser(
        user_id=user_id,
        team_id=team_id,
        enterprise_id=None,
        channel_id=user_id,
        bot_token="xoxb-test-token",
        ray_client_id=str(uuid4()),
        ray_username="test.user",
        ray_user_group_id=str(uuid4()),
        is_subscribed=True,
    )


def test_qe_additional_cost_distributes_across_priced_targets():
    from app.slack.evaluation_combined_quotes import qe_additional_cost

    costs = [
        {"file_uuid": "file-1", "language_uuid": "lang-fr"},
        {"file_uuid": "file-1", "language_uuid": "lang-es"},
    ]

    additional_costs = qe_additional_cost(80, costs)

    assert additional_costs == [
        {
            "label": "Quality Evaluation",
            "cost": 0.8,
            "file_uuid": "file-1",
            "language_uuid": "lang-fr",
        },
        {
            "label": "Quality Evaluation",
            "cost": 0.8,
            "file_uuid": "file-1",
            "language_uuid": "lang-es",
        },
    ]


def test_qe_additional_cost_honors_pricing_cost_subset():
    from app.slack.evaluation_combined_quotes import qe_additional_cost

    costs = [
        {"file_uuid": "file-1", "language_uuid": "lang-fr"},
        {"file_uuid": "file-1", "language_uuid": "lang-es"},
    ]

    additional_costs = qe_additional_cost(
        80,
        costs,
        pricing_costs=[costs[0]],
    )

    assert additional_costs == [
        {
            "label": "Quality Evaluation",
            "cost": 1.6,
            "file_uuid": "file-1",
            "language_uuid": "lang-fr",
        }
    ]


def test_combined_quote_net_savings_subtracts_qe_cost():
    from app.slack.evaluation_combined_quotes import (
        qe_additional_cost,
        validate_combined_quote_includes_qe_cost,
    )
    from app.slack.templates.blocks import combined_quote_net_savings

    costs = [
        {
            "file_uuid": "file-1",
            "language_uuid": "lang-fr",
            "service_list": [
                {
                    "estimated_cost": 80.0,
                    "quality_discount": {"savings": 12.0},
                }
            ],
        }
    ]
    additional_costs = qe_additional_cost(6, costs)

    assert (
        combined_quote_net_savings(costs, additional_costs, show_savings=True) == 11.88
    )
    summary = validate_combined_quote_includes_qe_cost(
        costs,
        6,
        show_savings=True,
    )
    assert summary["total_cost"] == 80.12
    assert summary["net_savings"] == 11.88


def test_combined_quote_net_savings_hidden_when_non_positive():
    from app.slack.evaluation_combined_quotes import qe_additional_cost
    from app.slack.templates.blocks import combined_quote_net_savings

    costs = [
        {
            "file_uuid": "file-1",
            "language_uuid": "lang-fr",
            "service_list": [
                {
                    "estimated_cost": 80.0,
                    "quality_discount": {"savings": 0.10},
                }
            ],
        }
    ]
    additional_costs = qe_additional_cost(6, costs)

    assert combined_quote_net_savings(costs, additional_costs, show_savings=True) == 0.0


def test_combined_qe_complete_status_message_includes_savings_when_positive():
    from app.slack.evaluation_combined_quotes import combined_qe_complete_status_message

    message = combined_qe_complete_status_message(
        total_cost=80.12,
        net_savings=11.88,
    )

    rendered = str(message.blocks)
    assert (
        message.text
        == "Final cost after Arbitr evaluation: USD $80.12 (saved $11.88)\n"
        "Your AI translation has been submitted to our network of native-speaking "
        "specialist linguists for review. Please refer to the estimated completion "
        "date above."
    )
    assert "Final cost after Arbitr evaluation: USD $80.12" in rendered
    assert "saved $11.88" in rendered
    assert "specialist linguists for review" in rendered
    assert "Human translation in progress" not in rendered


def test_combined_qe_complete_status_message_hides_non_positive_savings():
    from app.slack.evaluation_combined_quotes import combined_qe_complete_status_message

    message = combined_qe_complete_status_message(
        total_cost=80.12,
        net_savings=0.0,
    )

    rendered = str(message.blocks)
    assert message.text.startswith("Final cost after Arbitr evaluation: USD $80.12\n")
    assert "Final cost after Arbitr evaluation: USD $80.12" in rendered
    assert "saved $" not in rendered
    assert "specialist linguists for review" in rendered
    assert "Human translation in progress" not in rendered


def test_select_unsubmitted_languages_marks_unselected_targets_cancelled():
    from app.slack.evaluation_combined_quotes import select_unsubmitted_languages

    job_data = {
        "source_files": [
            {
                "file_uuid": "file-1",
                "target_files": [
                    {"language_uuid": "lang-fr"},
                    {"language_uuid": "lang-es"},
                ],
            }
        ]
    }

    selected_languages = select_unsubmitted_languages(job_data, ["file-1:lang-fr"])

    assert selected_languages == ["file-1:lang-fr"]
    assert job_data["source_files"][0]["target_files"] == [
        {"language_uuid": "lang-fr", "human_job_status": "Submitted"},
        {"language_uuid": "lang-es", "human_job_status": "Cancelled"},
    ]


@pytest.mark.asyncio
async def test_handle_combined_qe_complete_updates_quote_and_posts_final_quote(
    mock_slack_user,
):
    from app.slack.evaluation_combined_quotes import handle_combined_qe_complete

    job_uuid = str(uuid4())
    event = RayEvent(
        event="verify:slack:evaluate:complete",
        data={"client_id": mock_slack_user.ray_client_id, "job_uuid": job_uuid},
    )
    auth = RayEventAuth()
    auth.slack_user = mock_slack_user
    mock_client = AsyncMock()
    costs = [
        {
            "file_uuid": "file-1",
            "language_uuid": "lang-fr",
            "service_list": [
                {
                    "estimated_cost": 80.0,
                    "time_estimate_days": 2,
                    "quality_discount": {
                        "tier": "good",
                        "word_discount_rate": 0.3,
                        "savings": 12.0,
                        "pricing_cap_applied": False,
                    },
                }
            ],
        }
    ]

    with patch(
        "app.slack.evaluation_combined_quotes.get_evaluate_quote_session",
        new_callable=AsyncMock,
        return_value={
            "channel_id": "C123",
            "message_ts": "111.222",
            "quote_snapshot": {
                "auto_submit_human_job": True,
                "token_cost": 6,
            },
        },
    ):
        with patch(
            "app.slack.evaluation_combined_quotes.post_notification",
            new_callable=AsyncMock,
        ) as mock_post:
            result = await handle_combined_qe_complete(
                mock_client,
                event,
                auth,
                job_data={
                    "uuid": job_uuid,
                    "workflow_uuid": "workflow-123",
                    "source_files": [
                        {
                            "file_uuid": "file-1",
                            "filename": "file.docx",
                            "target_files": [{"language_uuid": "lang-fr"}],
                            "report": {"language_uuid": "source-uuid"},
                        }
                    ],
                    "target_languages": [{"uuid": "lang-fr", "name": "French"}],
                },
                costs=costs,
            )

    assert result is True
    mock_client.chat_update.assert_awaited_once()
    updated_blocks = str(mock_client.chat_update.await_args.kwargs["blocks"])
    assert "Quality:" not in updated_blocks
    assert "-30% off" not in updated_blocks
    assert "Final Cost" not in updated_blocks
    assert "Quality Evaluation: USD" not in updated_blocks
    assert "Quality Evaluation is complete" not in updated_blocks
    assert "download_ai_translations_action" not in updated_blocks
    mock_post.assert_awaited_once()
    status_message = mock_post.await_args.args[3]
    assert status_message.text.startswith(
        "Final cost after Arbitr evaluation: USD $80.12 (saved $11.88)\n"
    )
    status_blocks = str(status_message.blocks)
    assert "Final cost after Arbitr evaluation: USD $80.12" in status_blocks
    assert "Human translation in progress" not in status_blocks
    assert "saved $11.88" in status_blocks
    assert "specialist linguists for review" in status_blocks
    assert "Final Quote" not in status_blocks


@pytest.mark.asyncio
async def test_handle_combined_qe_complete_excludes_cancelled_targets(
    mock_slack_user,
):
    from app.slack.evaluation_combined_quotes import handle_combined_qe_complete

    job_uuid = str(uuid4())
    event = RayEvent(
        event="verify:slack:evaluate:complete",
        data={"client_id": mock_slack_user.ray_client_id, "job_uuid": job_uuid},
    )
    auth = RayEventAuth()
    auth.slack_user = mock_slack_user
    mock_client = AsyncMock()
    costs = [
        {
            "file_uuid": "file-1",
            "language_uuid": "lang-fr",
            "service_list": [
                {
                    "estimated_cost": 80.0,
                    "time_estimate_days": 2,
                    "quality_discount": {
                        "tier": "good",
                        "word_discount_rate": 0.3,
                        "savings": 12.0,
                        "pricing_cap_applied": False,
                    },
                }
            ],
        },
        {
            "file_uuid": "file-1",
            "language_uuid": "lang-es",
            "service_list": [
                {
                    "estimated_cost": 60.0,
                    "time_estimate_days": 3,
                    "quality_discount": {
                        "tier": "acceptable",
                        "word_discount_rate": 0.1,
                        "savings": 6.0,
                        "pricing_cap_applied": False,
                    },
                }
            ],
        },
    ]

    with patch(
        "app.slack.evaluation_combined_quotes.get_evaluate_quote_session",
        new_callable=AsyncMock,
        return_value={
            "channel_id": "C123",
            "message_ts": "111.222",
            "quote_snapshot": {
                "auto_submit_human_job": True,
                "token_cost": 4,
                "selected_languages": ["file-1:lang-fr"],
            },
        },
    ):
        with patch(
            "app.slack.evaluation_combined_quotes.post_notification",
            new_callable=AsyncMock,
        ) as mock_post:
            result = await handle_combined_qe_complete(
                mock_client,
                event,
                auth,
                job_data={
                    "uuid": job_uuid,
                    "workflow_uuid": "workflow-123",
                    "source_files": [
                        {
                            "file_uuid": "file-1",
                            "filename": "file.docx",
                            "target_files": [
                                {"language_uuid": "lang-fr"},
                                {"language_uuid": "lang-es"},
                            ],
                            "report": {"language_uuid": "source-uuid"},
                        }
                    ],
                    "target_languages": [
                        {"uuid": "lang-fr", "name": "French"},
                        {"uuid": "lang-es", "name": "Spanish"},
                    ],
                },
                costs=costs,
            )

    assert result is True
    updated_blocks = str(mock_client.chat_update.await_args.kwargs["blocks"])
    assert "Quality:" not in updated_blocks
    assert "-30% off" not in updated_blocks
    assert ">Cancelled" in updated_blocks
    assert "Spanish" in updated_blocks
    assert "USD$60.08" not in updated_blocks
    assert "USD$80.08" in updated_blocks
    assert "Final Cost" not in updated_blocks
    assert "download_ai_translations_action" not in updated_blocks
    status_message = mock_post.await_args.args[3]
    assert status_message.text.startswith(
        "Final cost after Arbitr evaluation: USD $80.08 (saved $11.92)\n"
    )
    status_blocks = str(status_message.blocks)
    assert "Final cost after Arbitr evaluation: USD $80.08" in status_blocks
    assert "Human translation in progress" not in status_blocks
    assert "saved $11.92" in status_blocks
    assert "specialist linguists for review" in status_blocks
    assert ">Cancelled" not in status_blocks
    assert "USD$80.08" not in status_blocks


@pytest.mark.asyncio
async def test_ray_events_ready_for_ai_quote(mock_slack_user, user_id, team_id):
    job_uuid = str(uuid4())
    event = RayEvent(
        event="verify:slack:evaluate:ready_for_ai_quote",
        data={"client_id": mock_slack_user.ray_client_id, "job_uuid": job_uuid},
    )
    mock_client = AsyncMock()

    with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
        with patch(
            "app.dependencies.resolve_slack_delivery_user",
            new_callable=AsyncMock,
            return_value=mock_slack_user,
        ):
            with patch("app.dependencies.get_demo_link", return_value=[]):
                with patch("app.routers.ray.AsyncWebClient", return_value=mock_client):
                    with patch(
                        "app.routers.ray.post_evaluate_service_quote",
                        new_callable=AsyncMock,
                    ) as mock_quote:
                        from app.routers.ray import ray_events

                        auth = RayEventAuth()
                        await auth.initialize(event, "valid-token")
                        await ray_events(event, auth)

                        mock_quote.assert_awaited_once()
                        assert mock_quote.await_args is not None
                        assert mock_quote.await_args.kwargs["job_uuid"] == job_uuid
                        assert (
                            mock_quote.await_args.kwargs["accept_action_id"]
                            == "evaluation_ai_quote_accept"
                        )


@pytest.mark.asyncio
async def test_post_combined_qe_human_quote_updates_original_message(mock_slack_user):
    job_uuid = str(uuid4())
    event = RayEvent(
        event="verify:slack:evaluate:ready_for_qe_quote",
        data={"client_id": mock_slack_user.ray_client_id, "job_uuid": job_uuid},
    )
    mock_client = AsyncMock()
    ray_client = MagicMock()
    job = {
        "data": {
            "uuid": job_uuid,
            "extra_info": {"slack_channel_id": "C123"},
            "workflow_uuid": HUMAN_VERIFICATION_WORKFLOW_UUID,
            "source_files": [
                {"file_uuid": "f1", "filename": "file.docx", "target_files": []}
            ],
            "target_languages": [{"uuid": "l1", "name": "French"}],
        }
    }
    session = {"channel_id": "C123", "message_ts": "111.222"}

    auth = RayEventAuth()
    auth.slack_user = mock_slack_user

    with (
        patch(
            "app.slack.evaluation_combined_quotes.get_ray_client",
            new_callable=AsyncMock,
        ) as mock_ray,
        patch(
            "app.slack.evaluation_combined_quotes.get_evaluation_job_quote",
            new_callable=AsyncMock,
            return_value={"services_costs": {"quality_evaluation": 80}, "token": 80},
        ),
        patch(
            "app.slack.evaluation_combined_quotes.get_evaluation_job",
            new_callable=AsyncMock,
            return_value=job,
        ),
        patch(
            "app.slack.evaluation_combined_quotes.get_job_pricing",
            new_callable=AsyncMock,
            return_value={
                "data": [
                    {
                        "file_uuid": "f1",
                        "language_uuid": "l1",
                        "service_list": [
                            {
                                "estimated_cost": 90.0,
                                "time_estimate_days": 2,
                                "quality_discount": {
                                    "tier": "bad",
                                    "word_discount_rate": 0.1,
                                    "savings": 10.0,
                                    "is_estimate": True,
                                },
                            }
                        ],
                    }
                ]
            },
        ) as mock_pricing,
        patch(
            "app.slack.evaluation_combined_quotes.get_evaluate_quote_session",
            new_callable=AsyncMock,
            return_value=session,
        ),
        patch(
            "app.slack.evaluation_combined_quotes.save_evaluate_quote_session",
            new_callable=AsyncMock,
        ) as mock_save,
        patch(
            "app.slack.evaluation_combined_quotes.post_notification",
            new_callable=AsyncMock,
        ) as mock_post,
    ):
        mock_ray.return_value = ray_client
        from app.slack.evaluation_combined_quotes import post_combined_qe_human_quote

        await post_combined_qe_human_quote(
            mock_client,
            event,
            auth,
            job_uuid=job_uuid,
        )

    mock_pricing.assert_awaited_once()
    assert mock_pricing.await_args.kwargs["assumed_quality_tier"] == "bad"
    mock_client.chat_update.assert_awaited_once()
    assert mock_client.chat_update.await_args.kwargs["channel"] == "C123"
    assert "Quality Evaluation: USD" not in str(
        mock_client.chat_update.await_args.kwargs["blocks"]
    )
    assert "USD$91.60" in str(mock_client.chat_update.await_args.kwargs["blocks"])
    assert "Quality: bad" not in str(
        mock_client.chat_update.await_args.kwargs["blocks"]
    )
    assert "saved $" not in str(mock_client.chat_update.await_args.kwargs["blocks"])
    assert "download_ai_translations_action" in str(
        mock_client.chat_update.await_args.kwargs["blocks"]
    )
    mock_post.assert_not_awaited()
    mock_save.assert_awaited_once()
    assert mock_save.await_args.kwargs["stage"] == "awaiting_qe"
    assert (
        mock_save.await_args.kwargs["quote_snapshot"]["auto_submit_human_job"] is True
    )


@pytest.mark.asyncio
async def test_post_preaccepted_ai_quote_auto_proceeds(mock_slack_user):
    job_uuid = str(uuid4())
    event = RayEvent(
        event="verify:slack:evaluate:ready_for_ai_quote",
        data={"client_id": mock_slack_user.ray_client_id, "job_uuid": job_uuid},
    )
    mock_client = AsyncMock()
    ray_client = MagicMock()
    quote = {"services_costs": {"ai_translation": 100}, "token": 100}
    job = {
        "data": {
            "uuid": job_uuid,
            "extra_info": {
                "slack_channel_id": "C123",
                "pdf_page_count": 2,
                "preaccepted_ai_translation_quote": True,
                "prequote_message_ts": "111.222",
            },
        }
    }

    auth = RayEventAuth()
    auth.slack_user = mock_slack_user

    with (
        patch(
            "app.slack.evaluation_quotes.get_ray_client",
            new_callable=AsyncMock,
        ) as mock_ray,
        patch(
            "app.slack.evaluation_quotes.get_evaluation_job_quote",
            new_callable=AsyncMock,
            return_value=quote,
        ),
        patch(
            "app.slack.evaluation_quotes.get_evaluation_job",
            new_callable=AsyncMock,
            return_value=job,
        ),
        patch(
            "app.slack.evaluation_quotes.update_evaluate_quote_slack_message",
            new_callable=AsyncMock,
        ) as mock_update,
        patch(
            "app.slack.evaluation_quotes.proceed_evaluation_job",
            new_callable=AsyncMock,
        ) as mock_proceed,
        patch(
            "app.slack.evaluation_quotes.save_evaluate_quote_session",
            new_callable=AsyncMock,
        ) as mock_save,
        patch(
            "app.slack.evaluation_quotes.post_notification",
            new_callable=AsyncMock,
        ) as mock_post,
    ):
        mock_ray.return_value = ray_client
        from app.slack.evaluation_quotes import post_evaluate_service_quote

        await post_evaluate_service_quote(
            mock_client,
            event,
            auth,
            job_uuid=job_uuid,
            service="ai_translation",
            service_label="AI Translation",
            accept_action_id="evaluation_ai_quote_accept",
            include_pdf_fee=True,
        )

    mock_update.assert_not_awaited()
    mock_proceed.assert_awaited_once_with(
        ray_client,
        job_uuid,
        token_cost=150,
        skip_quality_evaluation=True,
    )
    mock_save.assert_awaited_once()
    assert mock_save.await_args.kwargs["stage"] == "accepted_ai"
    mock_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_ray_events_evaluate_complete_ai_only(mock_slack_user, user_id, team_id):
    job_uuid = str(uuid4())
    event = RayEvent(
        event="verify:slack:evaluate:complete",
        data={
            "client_id": mock_slack_user.ray_client_id,
            "job_uuid": job_uuid,
            "ai_only": True,
        },
    )
    mock_job = {
        "data": {
            "uuid": job_uuid,
            "workflow_uuid": HUMAN_VERIFICATION_WORKFLOW_UUID,
            "human_job_in_progress": False,
            "source_files": [],
            "target_languages": [],
        }
    }
    mock_client = AsyncMock()

    with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
        with patch(
            "app.dependencies.resolve_slack_delivery_user",
            new_callable=AsyncMock,
            return_value=mock_slack_user,
        ):
            with patch("app.dependencies.get_demo_link", return_value=[]):
                with patch("app.routers.ray.AsyncWebClient", return_value=mock_client):
                    with patch(
                        "app.routers.ray.get_evaluation_job",
                        return_value=mock_job,
                    ):
                        with patch(
                            "app.routers.ray.claim_evaluate_complete_notification",
                            new_callable=AsyncMock,
                            return_value=True,
                        ):
                            with patch(
                                "app.routers.ray.post_notification",
                                new_callable=AsyncMock,
                            ) as mock_post:
                                with patch(
                                    "app.routers.ray.EvaluateAiOnlyCompleteMessage"
                                ) as mock_msg_cls:
                                    mock_msg_cls.return_value = MagicMock(
                                        text="AI ready", blocks=[]
                                    )
                                    from app.routers.ray import ray_events

                                    auth = RayEventAuth()
                                    await auth.initialize(event, "valid-token")
                                    await ray_events(event, auth)

                                    mock_msg_cls.assert_called_once_with(
                                        mock_job["data"]
                                    )
                                    mock_post.assert_called_once()


@pytest.mark.asyncio
async def test_ray_events_evaluate_complete_hv_uses_human_job_quote(
    mock_slack_user, user_id, team_id
):
    job_uuid = str(uuid4())
    event = RayEvent(
        event="verify:slack:evaluate:complete",
        data={
            "client_id": mock_slack_user.ray_client_id,
            "job_uuid": job_uuid,
            "tokens": 10,
        },
    )
    mock_job = {
        "data": {
            "uuid": job_uuid,
            "workflow_uuid": HUMAN_VERIFICATION_WORKFLOW_UUID,
            "human_job_in_progress": False,
            "source_files": [{"file_uuid": "f1"}],
            "target_languages": [{"uuid": "l1"}],
        }
    }
    mock_client = AsyncMock()

    with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
        with patch(
            "app.dependencies.resolve_slack_delivery_user",
            new_callable=AsyncMock,
            return_value=mock_slack_user,
        ):
            with patch("app.dependencies.get_demo_link", return_value=[]):
                with patch("app.routers.ray.AsyncWebClient", return_value=mock_client):
                    with patch(
                        "app.routers.ray.get_evaluation_job",
                        return_value=mock_job,
                    ):
                        with patch(
                            "app.routers.ray.claim_evaluate_complete_notification",
                            new_callable=AsyncMock,
                            return_value=True,
                        ):
                            with patch(
                                "app.routers.ray.get_ray_client",
                                new_callable=AsyncMock,
                                return_value=MagicMock(),
                            ):
                                with patch(
                                    "app.routers.ray.get_job_pricing",
                                    new_callable=AsyncMock,
                                    return_value={"data": []},
                                ):
                                    with patch(
                                        "app.routers.ray.post_notification",
                                        new_callable=AsyncMock,
                                    ):
                                        with patch(
                                            "app.routers.ray.HumanJobQuoteMessage"
                                        ) as mock_ht:
                                            mock_ht.return_value = MagicMock(
                                                text="HT quote", blocks=[]
                                            )
                                            from app.routers.ray import ray_events

                                            auth = RayEventAuth()
                                            await auth.initialize(event, "valid-token")
                                            await ray_events(event, auth)

                                            mock_ht.assert_called_once()
