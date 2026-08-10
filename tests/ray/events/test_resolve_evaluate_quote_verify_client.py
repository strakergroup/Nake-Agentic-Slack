"""RAY-81247 — evaluate quote Verify client when CRM is inactive."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.dependencies import RayEvent, RayEventAuth
from app.ray.events.evaluate_quote_events import resolve_evaluate_quote_verify_client


@pytest.mark.asyncio
async def test_resolve_evaluate_quote_verify_client_prefers_personal_crm():
    personal = MagicMock()
    personal.id = "A02F3A2F-C6D4-4D19-BD1E-BD0A5C990952"
    auth = RayEventAuth()
    auth.slack_user = MagicMock(
        user_id="UKVHQ6UJX",
        team_id="T03PE1PGBV5",
        enterprise_id="E04RDMG8XP1",
    )
    event = RayEvent(
        event="verify:slack:evaluate:ready_for_ai_quote",
        data={
            "client_id": "6BB48BEF-1EAD-4824-8724-5298CA10AA86",
            "job_uuid": "job-1",
        },
    )
    with patch(
        "app.ray.events.evaluate_quote_events.get_ray_client",
        new=AsyncMock(return_value=personal),
    ):
        client = await resolve_evaluate_quote_verify_client(auth, event)
    assert client is personal


@pytest.mark.asyncio
async def test_resolve_evaluate_quote_verify_client_falls_back_to_ht_sa():
    sa = MagicMock()
    sa.id = "6BB48BEF-1EAD-4824-8724-5298CA10AA86"
    auth = RayEventAuth()
    auth.slack_user = MagicMock(
        user_id="UKVHQ6UJX",
        team_id="T03PE1PGBV5",
        enterprise_id="E04RDMG8XP1",
    )
    event = RayEvent(
        event="verify:slack:evaluate:ready_for_ai_quote",
        data={
            "client_id": "6BB48BEF-1EAD-4824-8724-5298CA10AA86",
            "job_uuid": "job-1",
        },
    )
    with (
        patch(
            "app.ray.events.evaluate_quote_events.get_ray_client",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.ray.events.evaluate_quote_events.ht_service_account_member_uuid",
            return_value="6BB48BEF-1EAD-4824-8724-5298CA10AA86",
        ),
        patch(
            "app.ray.events.evaluate_quote_events.get_ht_service_account_ray_client",
            new=AsyncMock(return_value=sa),
        ) as mock_sa,
    ):
        client = await resolve_evaluate_quote_verify_client(auth, event)

    assert client is sa
    mock_sa.assert_awaited_once()
