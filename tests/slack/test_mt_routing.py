from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.slack.listener_actions import (
    create_service_language_mapping,
    get_mt_translation,
)


def test_create_service_language_mapping_allows_microsoft_override():
    """Non-fr-ca targets can still route to Microsoft when explicitly requested."""
    result = create_service_language_mapping(
        ["en"],
        {"en": "group-a:fr-ca:en"},
        {"en": "microsoft"},
    )
    assert result == {"microsoft": {"en": "group-a:fr-ca:en"}}


@pytest.mark.asyncio
async def test_direct_mt_routes_frca_source_to_microsoft_when_pair_glossary_exists(
    user_id, team_id, ray_client
):
    from app.auth.connector import RayConnection, RayContext, RaySuperGroup

    mock_client = AsyncMock()
    super_group = RaySuperGroup(
        id=str(uuid4()),
        name="Test Group",
        verify_organization_uuid=str(uuid4()),
        enable_verify_in_slack=False,
        slack_team_id=team_id,
        slack_enterprise_id=None,
    )
    context = RayContext(
        {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "D123",
            "ray": RayConnection(super_group=[super_group], client=ray_client),
        }
    )

    with (
        patch(
            "app.slack.listener_actions.require_mt_tokens",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.slack.listener_actions.resolve_language",
            new_callable=AsyncMock,
            side_effect=[["en"], ["fr-ca"]],
        ),
        patch(
            "app.slack.listener_actions.get_group_id",
            new_callable=AsyncMock,
            return_value="group-a:group-b",
        ),
        patch(
            "app.mt.service.get_client_groups",
            new_callable=AsyncMock,
            return_value=["group-a"],
        ),
        patch("app.mt.service.fetch_one", new_callable=AsyncMock) as mock_fetch_one,
        patch(
            "app.slack.listener_actions.send_mt_translation_request",
            new_callable=AsyncMock,
        ) as mock_send_mt,
    ):

        async def _fetch_one_side_effect(query, _engine):
            params = {name: bind.value for name, bind in query._bindparams.items()}
            if (
                params["engine"] == "microsoft"
                and params["sl"] == "fr-ca"
                and params["tl"] == "en"
            ):
                return {"terminology_id": "group-a:fr-ca:en"}
            return None

        mock_fetch_one.side_effect = _fetch_one_side_effect

        await get_mt_translation(
            mock_client,
            context,
            target_lang="en",
            source_lang="fr-ca",
            sentence="Bonjour compte GLSS",
        )

        mock_send_mt.assert_called_once()
        assert mock_send_mt.await_args.kwargs["service_language_mapping"] == {
            "microsoft": {"en": "group-a:fr-ca:en"}
        }
