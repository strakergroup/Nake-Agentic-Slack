"""Tests for RAY-79115 sequential evaluate quote ray events."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
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


def test_qe_additional_costs_from_quote_preserves_exact_pair_costs():
    from app.slack.evaluation_combined_quotes import (
        qe_additional_costs_from_quote,
    )

    additional_costs = qe_additional_costs_from_quote(
        {
            "details": [
                {
                    "file_uuid": "file-1",
                    "target_language_uuid": "lang-fr",
                    "token": 10,
                },
                {
                    "file_uuid": "file-1",
                    "target_language_uuid": "lang-es",
                    "token": 25,
                },
            ]
        },
        selected_pairs=["file-1:lang-es"],
    )

    assert additional_costs == [
        {
            "label": "Quality Evaluation",
            "cost": 0.5,
            "file_uuid": "file-1",
            "language_uuid": "lang-es",
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
        == "Final cost after AI quality evaluation: USD 80.12 (saved USD 11.88)\n"
        "Your AI translation has been submitted to our network of native-speaking "
        "specialist linguists for review. Please refer to the estimated completion "
        "date above."
    )
    assert "Final cost after AI quality evaluation: USD 80.12" in rendered
    assert "saved USD 11.88" in rendered
    assert "*Estimated Completion*" not in rendered
    assert "specialist linguists for review" in rendered
    assert "Human translation in progress" not in rendered


def test_combined_qe_complete_status_message_hides_non_positive_savings():
    from app.slack.evaluation_combined_quotes import combined_qe_complete_status_message

    message = combined_qe_complete_status_message(
        total_cost=80.12,
        net_savings=0.0,
    )

    rendered = str(message.blocks)
    assert message.text.startswith(
        "Final cost after AI quality evaluation: USD 80.12\n"
    )
    assert "Final cost after AI quality evaluation: USD 80.12" in rendered
    assert "saved USD " not in rendered
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
    # Post-QE line amounts stay on the original HT quote...
    assert "USD 80.12" in updated_blocks
    assert "French" in updated_blocks
    # ...with a single Estimated Completion from the quote panel...
    assert updated_blocks.count("Estimated Completion") == 1
    # ...and final-cost status on the same message (no separate post).
    assert "Final cost after AI quality evaluation: USD 80.12" in updated_blocks
    assert "saved USD 11.88" in updated_blocks
    assert "specialist linguists for review" in updated_blocks
    assert "Quality:" not in updated_blocks
    assert "download_ai_translations_action" not in updated_blocks
    assert "Accept Quote" not in updated_blocks
    mock_post.assert_not_awaited()


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
    # Selected-pair post-QE amounts remain; deselected stay Cancelled.
    assert "French" in updated_blocks
    assert "Spanish" in updated_blocks
    assert "Cancelled" in updated_blocks
    assert "USD 80.08" in updated_blocks
    assert "USD 60.08" not in updated_blocks
    assert updated_blocks.count("Estimated Completion") == 1
    assert "Final cost after AI quality evaluation: USD 80.08" in updated_blocks
    assert "saved USD 11.92" in updated_blocks
    assert "specialist linguists for review" in updated_blocks
    assert "download_ai_translations_action" not in updated_blocks
    mock_post.assert_not_awaited()


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
async def test_ai_quote_event_waits_for_adjustable_acceptance(mock_slack_user):
    job_uuid = str(uuid4())
    event = RayEvent(
        event="verify:slack:evaluate:ready_for_ai_quote",
        data={"client_id": mock_slack_user.ray_client_id, "job_uuid": job_uuid},
    )
    auth = RayEventAuth()
    auth.slack_user = mock_slack_user

    with (
        patch(
            "app.ray.events.evaluate_quote_events.get_verify_languages",
            new_callable=AsyncMock,
            return_value=[{"uuid": "lang-1", "name": "French"}],
        ),
        patch(
            "app.ray.events.evaluate_quote_events.get_ray_client",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "app.ray.events.evaluate_quote_events.get_evaluation_job_quote",
            new_callable=AsyncMock,
            return_value={
                "services_costs": {"ai_translation": 100},
                "details": [
                    {
                        "file_uuid": "file-1",
                        "target_language_uuid": "lang-1",
                        "token": 100,
                    }
                ],
            },
        ),
        patch(
            "app.ray.events.evaluate_quote_events.get_evaluation_job",
            new_callable=AsyncMock,
            return_value={
                "data": {
                    "uuid": job_uuid,
                    "extra_info": {"slack_channel_id": "C123"},
                    "target_languages": [{"uuid": "lang-1"}],
                    "source_files": [
                        {"file_uuid": "file-1", "filename": "source.docx"}
                    ],
                }
            },
        ),
        patch(
            "app.ray.events.evaluate_quote_events.post_notification",
            new_callable=AsyncMock,
            return_value={"ts": "111.222"},
        ) as mock_post,
        patch(
            "app.ray.events.evaluate_quote_events.proceed_evaluation_job",
            new_callable=AsyncMock,
        ) as mock_proceed,
        patch(
            "app.ray.events.evaluate_quote_events.save_evaluate_quote_session",
            new_callable=AsyncMock,
        ) as mock_save,
    ):
        from app.ray.events.evaluate_quote_events import post_evaluate_service_quote

        await post_evaluate_service_quote(
            AsyncMock(),
            event,
            auth,
            job_uuid=job_uuid,
            service="ai_translation",
            service_label="AI Translation",
            accept_action_id="evaluation_ai_quote_accept",
            include_pdf_fee=True,
        )

    message = mock_post.await_args.args[3]
    rendered = str(message.blocks)
    assert "*Total cost:* USD 2.00" in rendered
    assert ":paperclip: *source.docx*" in rendered
    assert "*French*\\n>USD 2.00" in rendered
    assert "Adjust Request" in rendered
    assert "Accept Quote" in rendered
    assert "Estimated Completion" not in rendered
    assert "Due" not in rendered
    mock_proceed.assert_not_awaited()
    assert mock_save.await_args.kwargs["stage"] == "awaiting_ai"


@pytest.mark.asyncio
async def test_auto_qe_fetches_quote_before_claim_and_releases_on_402(
    mock_slack_user,
):
    """Quote fetch must precede claim; definite 402 releases for retry."""
    from app.api.verify import VerifyAPIError

    job_uuid = str(uuid4())
    event = RayEvent(
        event="verify:slack:evaluate:ready_for_qe_quote",
        data={"client_id": mock_slack_user.ray_client_id, "job_uuid": job_uuid},
    )
    mock_client = AsyncMock()
    job = {
        "data": {
            "uuid": job_uuid,
            "extra_info": {
                "slack_channel_id": "C123",
                "slack_ht_quote_after_qe": True,
                "ai_translation_file_and_languages": ["f1:l1"],
            },
            "source_files": [
                {"file_uuid": "f1", "filename": "file.docx", "target_files": []}
            ],
            "target_languages": [{"uuid": "l1", "name": "French"}],
        }
    }
    auth = RayEventAuth()
    auth.slack_user = mock_slack_user
    call_order: list[str] = []

    async def _quote(*_args, **_kwargs):
        call_order.append("quote")
        return {"services_costs": {"quality_evaluation": 80}, "token": 80}

    async def _claim(*_args, **_kwargs):
        call_order.append("claim")
        return True

    with (
        patch(
            "app.slack.evaluation_combined_quotes.get_ray_client",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "app.slack.evaluation_combined_quotes.get_evaluation_job",
            new_callable=AsyncMock,
            return_value=job,
        ),
        patch(
            "app.slack.evaluation_combined_quotes.get_evaluation_job_quote",
            new=_quote,
        ),
        patch(
            "app.slack.evaluation_combined_quotes.claim_ray_event_notification",
            new=_claim,
        ),
        patch(
            "app.slack.evaluation_combined_quotes.release_ray_event_notification",
            new_callable=AsyncMock,
        ) as mock_release,
        patch(
            "app.slack.evaluation_combined_quotes.proceed_quality_evaluation",
            new_callable=AsyncMock,
            side_effect=VerifyAPIError("Insufficient AI token balance.", 402),
        ),
        patch(
            "app.slack.evaluation_combined_quotes.save_evaluate_quote_session",
            new_callable=AsyncMock,
        ) as mock_save,
    ):
        from app.slack.evaluation_combined_quotes import post_combined_qe_human_quote

        await post_combined_qe_human_quote(
            mock_client,
            event,
            auth,
            job_uuid=job_uuid,
        )

    assert call_order == ["quote", "claim"]
    mock_release.assert_awaited_once()
    mock_save.assert_not_awaited()
    mock_client.chat_postMessage.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_combined_qe_human_quote_updates_ai_and_posts_new_message(
    mock_slack_user,
):
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
            "extra_info": {
                "slack_channel_id": "C123",
                "ai_translation_file_and_languages": ["f1:l1"],
            },
            "workflow_uuid": HUMAN_VERIFICATION_WORKFLOW_UUID,
            "source_files": [
                {"file_uuid": "f1", "filename": "file.docx", "target_files": []}
            ],
            "target_languages": [{"uuid": "l1", "name": "French"}],
        }
    }
    session = {
        "channel_id": "C123",
        "message_ts": "111.222",
        "quote_snapshot": {
            "service_label": "AI Translation",
            "token_cost": 100,
            "accept_action_id": "evaluation_ai_quote_accept",
            "ai_translation_file_and_languages": ["f1:l1"],
            "language_costs": [
                {
                    "file_uuid": "f1",
                    "file_label": "file.docx",
                    "value": "l1",
                    "label": "French",
                    "token": 100,
                }
            ],
        },
    }

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
            # AsyncSlackResponse is not a dict; ts must still be captured.
            return_value=type(
                "SlackResponse",
                (),
                {
                    "get": lambda self, key, default=None: {"ts": "333.444"}.get(
                        key, default
                    )
                },
            )(),
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
    assert mock_client.chat_update.await_args.kwargs["ts"] == "111.222"
    ai_updated = str(mock_client.chat_update.await_args.kwargs["blocks"])
    assert "AI translation is complete" in ai_updated
    assert "Review the human translation quote below." in ai_updated
    assert "*AI Translation:*" in ai_updated
    assert "*French*" in ai_updated
    assert "*Service:*" not in ai_updated
    assert "download_ai_translations_action" not in ai_updated
    mock_post.assert_awaited_once()
    ht_blocks = str(mock_post.await_args.args[3].blocks)
    assert "Quality Evaluation: USD" not in ht_blocks
    assert "USD 91.60" in ht_blocks
    assert "Quality: bad" not in ht_blocks
    assert "saved USD " not in ht_blocks
    assert "download_ai_translations_action" in ht_blocks
    mock_save.assert_awaited_once()
    assert mock_save.await_args.kwargs["stage"] == "awaiting_qe"
    assert mock_save.await_args.kwargs["message_ts"] == "333.444"
    assert mock_save.await_args.kwargs["ai_message_ts"] == "111.222"
    assert (
        mock_save.await_args.kwargs["quote_snapshot"]["auto_submit_human_job"] is True
    )


@pytest.mark.asyncio
async def test_post_combined_qe_human_quote_keeps_asymmetric_ai_scope(mock_slack_user):
    """Deselected AI pairs must stay Cancelled, not re-expand into a file×lang grid."""
    job_uuid = str(uuid4())
    event = RayEvent(
        event="verify:slack:evaluate:ready_for_qe_quote",
        data={"client_id": mock_slack_user.ray_client_id, "job_uuid": job_uuid},
    )
    mock_client = AsyncMock()
    auth = RayEventAuth()
    auth.slack_user = mock_slack_user
    job = {
        "data": {
            "uuid": job_uuid,
            "extra_info": {
                "slack_channel_id": "C123",
                "ai_translation_file_and_languages": [
                    "f1:lang-hi",
                    "f2:lang-ko",
                ],
            },
            "workflow_uuid": HUMAN_VERIFICATION_WORKFLOW_UUID,
            "source_files": [
                {
                    "file_uuid": "f1",
                    "filename": "a.txt",
                    "target_files": [
                        {"language_uuid": "lang-hi"},
                        {"language_uuid": "lang-ko"},
                    ],
                },
                {
                    "file_uuid": "f2",
                    "filename": "b.docx",
                    "target_files": [
                        {"language_uuid": "lang-hi"},
                        {"language_uuid": "lang-ko"},
                    ],
                },
            ],
            "target_languages": [
                {"uuid": "lang-hi", "name": "Hindi"},
                {"uuid": "lang-ko", "name": "Korean"},
            ],
        }
    }
    pricing_rows = [
        {
            "file_uuid": file_uuid,
            "language_uuid": language_uuid,
            "service_list": [
                {
                    "estimated_cost": cost,
                    "time_estimate_days": 1,
                    "quality_discount": {
                        "tier": "bad",
                        "word_discount_rate": 0.1,
                        "savings": 1.0,
                        "is_estimate": True,
                    },
                }
            ],
        }
        for file_uuid, language_uuid, cost in (
            ("f1", "lang-hi", 2.0),
            ("f1", "lang-ko", 99.0),
            ("f2", "lang-hi", 99.0),
            ("f2", "lang-ko", 40.0),
        )
    ]

    with (
        patch(
            "app.slack.evaluation_combined_quotes.get_ray_client",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "app.slack.evaluation_combined_quotes.get_evaluation_job_quote",
            new_callable=AsyncMock,
            return_value={"services_costs": {"quality_evaluation": 10}, "token": 10},
        ),
        patch(
            "app.slack.evaluation_combined_quotes.get_evaluation_job",
            new_callable=AsyncMock,
            return_value=job,
        ),
        patch(
            "app.slack.evaluation_combined_quotes.get_job_pricing",
            new_callable=AsyncMock,
            return_value={"data": pricing_rows},
        ),
        patch(
            "app.slack.evaluation_combined_quotes.get_evaluate_quote_session",
            new_callable=AsyncMock,
            return_value={
                "channel_id": "C123",
                "message_ts": "111.222",
                "quote_snapshot": {
                    "service_label": "AI Translation",
                    "token_cost": 50,
                    "accept_action_id": "evaluation_ai_quote_accept",
                    "ai_translation_file_and_languages": [
                        "f1:lang-hi",
                        "f2:lang-ko",
                    ],
                    "all_language_costs": [
                        {
                            "file_uuid": "f1",
                            "file_label": "a.txt",
                            "value": "lang-hi",
                            "label": "Hindi",
                            "token": 25,
                        },
                        {
                            "file_uuid": "f1",
                            "file_label": "a.txt",
                            "value": "lang-ko",
                            "label": "Korean",
                            "token": 25,
                        },
                        {
                            "file_uuid": "f2",
                            "file_label": "b.docx",
                            "value": "lang-hi",
                            "label": "Hindi",
                            "token": 25,
                        },
                        {
                            "file_uuid": "f2",
                            "file_label": "b.docx",
                            "value": "lang-ko",
                            "label": "Korean",
                            "token": 25,
                        },
                    ],
                },
            },
        ),
        patch(
            "app.slack.evaluation_combined_quotes.save_evaluate_quote_session",
            new_callable=AsyncMock,
        ),
        patch(
            "app.slack.evaluation_combined_quotes.post_notification",
            new_callable=AsyncMock,
            return_value={"ts": "333.444"},
        ) as mock_post,
    ):
        from app.slack.evaluation_combined_quotes import post_combined_qe_human_quote

        await post_combined_qe_human_quote(
            mock_client,
            event,
            auth,
            job_uuid=job_uuid,
        )

    ai_updated = str(mock_client.chat_update.await_args.kwargs["blocks"])
    assert "*AI Translation:*" in ai_updated
    assert ai_updated.count("Cancelled") == 2
    assert "*Service:*" not in ai_updated
    rendered = str(mock_post.await_args.args[3].blocks)
    assert "*Hindi*\\n>USD 2.10" in rendered or "*Hindi*\n>USD 2.10" in rendered
    assert "*Korean*\\n>USD 40.10" in rendered or "*Korean*\n>USD 40.10" in rendered
    assert "USD 99" not in rendered
    assert rendered.count("Cancelled") == 2
    assert "Maximum Total Cost*: USD 42.20" in rendered
    assert mock_client.chat_update.await_args.kwargs["ts"] == "111.222"


def _preaccepted_ai_quote_fixtures(job_uuid: str):
    quote = {
        "services_costs": {"ai_translation": 100},
        "token": 100,
        "details": [
            {"file_uuid": "file-1", "target_language_uuid": "lang-1", "token": 60},
            {"file_uuid": "file-1", "target_language_uuid": "lang-2", "token": 40},
        ],
    }
    job = {
        "data": {
            "uuid": job_uuid,
            "extra_info": {
                "slack_channel_id": "C123",
                "pdf_page_count": 2,
                "preaccepted_ai_translation_quote": True,
                "prequote_message_ts": "111.222",
            },
            "target_languages": [{"uuid": "lang-1"}, {"uuid": "lang-2"}],
            "source_files": [{"file_uuid": "file-1", "filename": "source.pdf"}],
        }
    }
    return quote, job


def _patch_preaccepted_ai_quote(
    *,
    quote: dict,
    job: dict,
    ray_client,
    claim_results: list,
    save_side_effect=None,
    proceed_side_effect=None,
):
    """Patch the collaborators of the PDF preaccepted AI quote path."""
    redis_stub = MagicMock()
    redis_stub.set = AsyncMock(side_effect=claim_results)
    redis_stub.delete = AsyncMock(return_value=1)
    return (
        patch(
            "app.ray.events.evaluate_quote_events.redis_conn",
            redis_stub,
        ),
        patch(
            "app.ray.events.evaluate_quote_events.get_verify_languages",
            new_callable=AsyncMock,
            return_value=[
                {"uuid": "lang-1", "name": "French"},
                {"uuid": "lang-2", "name": "German"},
            ],
        ),
        patch(
            "app.ray.events.evaluate_quote_events.get_ray_client",
            new_callable=AsyncMock,
            return_value=ray_client,
        ),
        patch(
            "app.ray.events.evaluate_quote_events.get_evaluation_job_quote",
            new_callable=AsyncMock,
            return_value=quote,
        ),
        patch(
            "app.ray.events.evaluate_quote_events.get_evaluation_job",
            new_callable=AsyncMock,
            return_value=job,
        ),
        patch(
            "app.ray.events.evaluate_quote_events.proceed_evaluation_job",
            new_callable=AsyncMock,
            side_effect=proceed_side_effect,
        ),
        patch(
            "app.ray.events.evaluate_quote_events.save_evaluate_quote_session",
            new_callable=AsyncMock,
            side_effect=save_side_effect,
        ),
        patch(
            "app.ray.events.evaluate_quote_events.post_notification",
            new_callable=AsyncMock,
        ),
    )


@pytest.mark.asyncio
async def test_post_preaccepted_ai_quote_auto_proceeds(mock_slack_user):
    """The PDF pre-quote debits on arrival and keeps its per-pair cost rows."""
    job_uuid = str(uuid4())
    event = RayEvent(
        event="verify:slack:evaluate:ready_for_ai_quote",
        data={"client_id": mock_slack_user.ray_client_id, "job_uuid": job_uuid},
    )
    mock_client = AsyncMock()
    ray_client = MagicMock()
    quote, job = _preaccepted_ai_quote_fixtures(job_uuid)

    auth = RayEventAuth()
    auth.slack_user = mock_slack_user

    (
        redis_patch,
        languages_patch,
        ray_patch,
        quote_patch,
        job_patch,
        proceed_patch,
        save_patch,
        post_patch,
    ) = _patch_preaccepted_ai_quote(
        quote=quote,
        job=job,
        ray_client=ray_client,
        claim_results=[True],
    )

    with (
        redis_patch,
        languages_patch,
        ray_patch,
        quote_patch,
        job_patch,
        proceed_patch as mock_proceed,
        save_patch as mock_save,
        post_patch as mock_post,
    ):
        from app.ray.events.evaluate_quote_events import post_evaluate_service_quote

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

    mock_proceed.assert_awaited_once_with(
        ray_client,
        job_uuid,
        token_cost=150,
        skip_quality_evaluation=True,
        ai_translation_file_and_languages=["file-1:lang-1", "file-1:lang-2"],
    )
    mock_save.assert_awaited_once()
    assert mock_save.await_args.kwargs["stage"] == "accepted_ai"
    snapshot = mock_save.await_args.kwargs["quote_snapshot"]
    # The Adjust Request modal and the AI quote refresh read these keys; the
    # preaccepted path used to save none of them.
    assert [row["label"] for row in snapshot["all_language_costs"]] == [
        "French",
        "German",
    ]
    assert [row["token"] for row in snapshot["language_costs"]] == [60, 40]
    assert snapshot["ai_quote_details"] == quote["details"]
    assert snapshot["file_uuids"] == ["file-1"]
    mock_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_preaccepted_ai_quote_debits_once_on_redelivery(mock_slack_user):
    """A redelivered ready_for_ai_quote must not charge the client twice."""
    job_uuid = str(uuid4())
    event = RayEvent(
        event="verify:slack:evaluate:ready_for_ai_quote",
        data={"client_id": mock_slack_user.ray_client_id, "job_uuid": job_uuid},
    )
    mock_client = AsyncMock()
    quote, job = _preaccepted_ai_quote_fixtures(job_uuid)

    auth = RayEventAuth()
    auth.slack_user = mock_slack_user

    (
        redis_patch,
        languages_patch,
        ray_patch,
        quote_patch,
        job_patch,
        proceed_patch,
        save_patch,
        post_patch,
    ) = _patch_preaccepted_ai_quote(
        quote=quote,
        job=job,
        ray_client=MagicMock(),
        # First delivery wins the NX claim; the redelivery misses it.
        claim_results=[True, None],
    )

    with (
        redis_patch,
        languages_patch,
        ray_patch,
        quote_patch,
        job_patch,
        proceed_patch as mock_proceed,
        save_patch as mock_save,
        post_patch,
    ):
        from app.ray.events.evaluate_quote_events import post_evaluate_service_quote

        for _delivery in range(2):
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

    assert mock_proceed.await_count == 1
    assert mock_save.await_count == 1


@pytest.mark.asyncio
async def test_preaccepted_ai_quote_session_failure_after_debit_is_not_retryable(
    mock_slack_user,
):
    """A Redis blip after the debit must not bubble a retryable error."""
    job_uuid = str(uuid4())
    event = RayEvent(
        event="verify:slack:evaluate:ready_for_ai_quote",
        data={"client_id": mock_slack_user.ray_client_id, "job_uuid": job_uuid},
    )
    mock_client = AsyncMock()
    quote, job = _preaccepted_ai_quote_fixtures(job_uuid)

    auth = RayEventAuth()
    auth.slack_user = mock_slack_user

    (
        redis_patch,
        languages_patch,
        ray_patch,
        quote_patch,
        job_patch,
        proceed_patch,
        save_patch,
        post_patch,
    ) = _patch_preaccepted_ai_quote(
        quote=quote,
        job=job,
        ray_client=MagicMock(),
        claim_results=[True],
        save_side_effect=ConnectionError("redis unavailable"),
    )

    with (
        redis_patch,
        languages_patch,
        ray_patch,
        quote_patch,
        job_patch,
        proceed_patch as mock_proceed,
        save_patch,
        post_patch,
        patch("app.ray.events.evaluate_quote_events.notify_exception") as mock_notify,
    ):
        from app.ray.events.evaluate_quote_events import post_evaluate_service_quote

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

    mock_proceed.assert_awaited_once()
    mock_notify.assert_called_once()


@pytest.mark.asyncio
async def test_preaccepted_ai_quote_timeout_does_not_escape_to_router(mock_slack_user):
    """An ambiguous proceed() failure is reported, never re-raised for retry."""
    job_uuid = str(uuid4())
    event = RayEvent(
        event="verify:slack:evaluate:ready_for_ai_quote",
        data={"client_id": mock_slack_user.ray_client_id, "job_uuid": job_uuid},
    )
    mock_client = AsyncMock()
    quote, job = _preaccepted_ai_quote_fixtures(job_uuid)

    auth = RayEventAuth()
    auth.slack_user = mock_slack_user

    (
        redis_patch,
        languages_patch,
        ray_patch,
        quote_patch,
        job_patch,
        proceed_patch,
        save_patch,
        post_patch,
    ) = _patch_preaccepted_ai_quote(
        quote=quote,
        job=job,
        ray_client=MagicMock(),
        claim_results=[True],
        proceed_side_effect=httpx.ReadTimeout("verify timed out"),
    )

    with (
        redis_patch,
        languages_patch,
        ray_patch,
        quote_patch,
        job_patch,
        proceed_patch,
        save_patch as mock_save,
        post_patch,
        patch("app.ray.events.evaluate_quote_events.notify_exception"),
    ):
        from app.ray.events.evaluate_quote_events import post_evaluate_service_quote

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

    mock_save.assert_not_awaited()
    mock_client.chat_postMessage.assert_awaited_once()


@pytest.mark.asyncio
async def test_preaccepted_ai_quote_releases_claim_on_insufficient_balance(
    mock_slack_user,
):
    """Definite 402 before debit side effects must release the claim for retry."""
    from app.api.verify import VerifyAPIError

    job_uuid = str(uuid4())
    event = RayEvent(
        event="verify:slack:evaluate:ready_for_ai_quote",
        data={"client_id": mock_slack_user.ray_client_id, "job_uuid": job_uuid},
    )
    mock_client = AsyncMock()
    quote, job = _preaccepted_ai_quote_fixtures(job_uuid)
    # Non-admin auto path uses the same preaccepted proceed helper.
    job["data"]["extra_info"]["slack_ht_quote_after_qe"] = True
    job["data"]["extra_info"]["preaccepted_ai_translation_quote"] = True

    auth = RayEventAuth()
    auth.slack_user = mock_slack_user

    (
        redis_patch,
        languages_patch,
        ray_patch,
        quote_patch,
        job_patch,
        proceed_patch,
        save_patch,
        post_patch,
    ) = _patch_preaccepted_ai_quote(
        quote=quote,
        job=job,
        ray_client=MagicMock(),
        claim_results=[True],
        proceed_side_effect=VerifyAPIError("Insufficient AI token balance.", 402),
    )

    with (
        redis_patch as redis_conn,
        languages_patch,
        ray_patch,
        quote_patch,
        job_patch,
        proceed_patch,
        save_patch as mock_save,
        post_patch,
    ):
        from app.ray.events.evaluate_quote_events import post_evaluate_service_quote

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

    mock_save.assert_not_awaited()
    redis_conn.delete.assert_awaited_once()
    mock_client.chat_postMessage.assert_awaited_once()


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
                            "app.routers.ray.claim_ray_event_notification",
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
                            "app.routers.ray.claim_ray_event_notification",
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
                                            "app.routers.ray.standalone_ht_quote_message"
                                        ) as mock_ht:
                                            mock_ht.return_value = MagicMock(
                                                text="HT quote", blocks=[]
                                            )
                                            from app.routers.ray import ray_events

                                            auth = RayEventAuth()
                                            await auth.initialize(event, "valid-token")
                                            await ray_events(event, auth)

                                            mock_ht.assert_called_once()


@pytest.mark.asyncio
async def test_ray_events_evaluate_complete_ht_quote_uses_stored_channel(
    mock_slack_user, user_id, team_id
):
    """Non-admin HT quotes must post to slack_channel_id, not only the DM."""
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
            "workflow_uuid": None,
            "human_job_in_progress": False,
            "source_files": [{"file_uuid": "f1"}],
            "target_languages": [{"uuid": "l1"}],
            "extra_info": {
                "slack_ht_quote_after_qe": True,
                "slack_channel_id": "C-EVAL-CHANNEL",
            },
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
                            "app.routers.ray.claim_ray_event_notification",
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
                                        "app.routers.ray.handle_combined_qe_complete",
                                        new_callable=AsyncMock,
                                        return_value=False,
                                    ):
                                        with patch(
                                            "app.routers.ray.post_notification",
                                            new_callable=AsyncMock,
                                        ) as mock_post:
                                            with patch(
                                                "app.routers.ray.standalone_ht_quote_message"
                                            ) as mock_ht:
                                                mock_ht.return_value = MagicMock(
                                                    text="HT quote", blocks=[]
                                                )
                                                from app.routers.ray import ray_events

                                                auth = RayEventAuth()
                                                await auth.initialize(
                                                    event, "valid-token"
                                                )
                                                await ray_events(event, auth)

                                                mock_ht.assert_called_once()
                                                assert (
                                                    mock_post.await_args.kwargs[
                                                        "channel_id"
                                                    ]
                                                    == "C-EVAL-CHANNEL"
                                                )
