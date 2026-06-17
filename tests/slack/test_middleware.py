from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from slack_bolt.context.async_context import AsyncBoltContext

from app.auth.connector import RayConnection, RaySuperGroup
from app.slack.middleware import ray_connection, require_mt_tokens, require_ray_client
from app.slack.templates.messages import LoginMessage


class TestRayConnectionMiddleware:
    """Tests for ray_connection listener middleware."""

    @pytest.mark.asyncio
    async def test_ray_connection_without_user_uses_workspace_super_group(
        self, team_id, channel_id
    ):
        """Test bot events without user context can still load workspace settings."""
        context = AsyncBoltContext(team_id=team_id, channel_id=channel_id)
        super_group = RaySuperGroup(
            id="sg-123",
            name="Test Group",
            slack_team_id=team_id,
            verify_organization_uuid="org-123",
            slack_enterprise_id=None,
        )
        next_mock = AsyncMock()

        with patch(
            "app.slack.middleware.get_ray_super_group", new_callable=AsyncMock
        ) as mock_get_super_group:
            mock_get_super_group.return_value = [super_group]

            await ray_connection(context, next_mock)

            assert context["ray"] == RayConnection(
                super_group=[super_group], client=None
            )
            assert context["is_bot"] is True
            next_mock.assert_awaited_once()


class TestRequireRayClient:
    """Tests for require_ray_client middleware helper function."""

    @pytest.mark.asyncio
    async def test_require_ray_client_with_connected_client(self, context, ray_client):
        """Test that require_ray_client returns True when client is connected."""
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context["ray"] = ray_connection
        context["login_prompt"] = LoginMessage(
            user_id=context["user_id"],
            team_id=context["team_id"],
            enterprise_id=context.enterprise_id,
            channel_id=context["channel_id"],
            ray_client=ray_client,
        )

        result = await require_ray_client(context, prompt_login=False)
        assert result is True

    @pytest.mark.asyncio
    async def test_require_ray_client_without_client_no_prompt(self, context):
        """Test that require_ray_client returns False when no client and prompt_login=False."""
        context["ray"] = RayConnection(super_group=[], client=None)
        context["login_prompt"] = LoginMessage(
            user_id=context["user_id"],
            team_id=context["team_id"],
            enterprise_id=context.enterprise_id,
            channel_id=context["channel_id"],
        )

        result = await require_ray_client(context, prompt_login=False)
        assert result is False

    @pytest.mark.asyncio
    async def test_require_ray_client_without_client_with_prompt_respond(self, context):
        """Test that require_ray_client sends login prompt via respond when available."""
        context["ray"] = RayConnection(super_group=[], client=None)
        login_message = LoginMessage(
            user_id=context["user_id"],
            team_id=context["team_id"],
            enterprise_id=context.enterprise_id,
            channel_id=context["channel_id"],
        )
        context["login_prompt"] = login_message
        context["response_url"] = "https://hooks.slack.com/test"
        mock_respond = AsyncMock()
        with patch.object(
            context.__class__,
            "respond",
            new_callable=PropertyMock,
            return_value=mock_respond,
        ):
            result = await require_ray_client(context, prompt_login=True)
            assert result is False
            mock_respond.assert_called_once()
            call_args = mock_respond.call_args
            assert call_args[1]["text"] == login_message.text
            assert call_args[1]["blocks"] == login_message.blocks

    @pytest.mark.asyncio
    async def test_require_ray_client_without_client_with_prompt_ephemeral(
        self, context
    ):
        """Test that require_ray_client sends ephemeral message when respond not available."""
        context["ray"] = RayConnection(super_group=[], client=None)
        login_message = LoginMessage(
            user_id=context["user_id"],
            team_id=context["team_id"],
            enterprise_id=context.enterprise_id,
            channel_id=context["channel_id"],
        )
        context["login_prompt"] = login_message
        context["response_url"] = None
        mock_client = AsyncMock()
        mock_client.chat_postEphemeral = AsyncMock()
        with (
            patch.object(
                context.__class__,
                "respond",
                new_callable=PropertyMock,
                return_value=None,
            ),
            patch.object(
                context.__class__,
                "client",
                new_callable=PropertyMock,
                return_value=mock_client,
            ),
        ):
            result = await require_ray_client(context, prompt_login=True)
            assert result is False
            mock_client.chat_postEphemeral.assert_called_once()
            call_args = mock_client.chat_postEphemeral.call_args
            assert call_args[1]["text"] == login_message.text
            assert call_args[1]["blocks"] == login_message.blocks

    @pytest.mark.asyncio
    async def test_require_ray_client_with_variation(self, context):
        """Test that require_ray_client uses variation when provided."""
        context["ray"] = RayConnection(super_group=[], client=None)
        login_message = LoginMessage(
            user_id=context["user_id"],
            team_id=context["team_id"],
            enterprise_id=context.enterprise_id,
            channel_id=context["channel_id"],
        )
        context["login_prompt"] = login_message
        context["response_url"] = "https://hooks.slack.com/test"
        mock_respond = AsyncMock()
        variation = LoginMessage.GET_JOB
        with patch.object(
            context.__class__,
            "respond",
            new_callable=PropertyMock,
            return_value=mock_respond,
        ):
            result = await require_ray_client(
                context, prompt_login=True, variation=variation
            )
            assert result is False
            mock_respond.assert_called_once()
            # Verify variation was applied
            call_args = mock_respond.call_args
            assert call_args[1]["text"] == login_message.with_variation(variation).text

    @pytest.mark.asyncio
    async def test_require_ray_client_no_login_prompt_in_context(self, context):
        """Test that require_ray_client handles missing login_prompt gracefully."""
        context["ray"] = RayConnection(super_group=[], client=None)
        context["login_prompt"] = None
        mock_client = AsyncMock()
        with patch.object(
            context.__class__,
            "client",
            new_callable=PropertyMock,
            return_value=mock_client,
        ):
            result = await require_ray_client(context, prompt_login=True)
            assert result is False

    @pytest.mark.asyncio
    async def test_require_ray_client_no_client_object(self, context):
        """Test that require_ray_client returns False when ray is not RayConnection."""
        context["ray"] = None
        context["login_prompt"] = LoginMessage(
            user_id=context["user_id"],
            team_id=context["team_id"],
            enterprise_id=context.enterprise_id,
            channel_id=context["channel_id"],
        )
        mock_client = AsyncMock()
        with patch.object(
            context.__class__,
            "client",
            new_callable=PropertyMock,
            return_value=mock_client,
        ):
            result = await require_ray_client(context, prompt_login=True)
            assert result is False


class TestRequireMtTokens:
    """Tests for require_mt_tokens middleware helper function."""

    @pytest.mark.asyncio
    @patch("app.slack.middleware.get_client_tokens")
    @patch("app.slack.middleware.is_ibm_enterprise")
    async def test_require_mt_tokens_with_sufficient_client_tokens(
        self, mock_is_ibm, mock_get_client_tokens, context, ray_client
    ):
        """Test that require_mt_tokens returns True when client has sufficient tokens."""
        from app.auth.connector import GetCreditBalanceResponse

        ray_connection = RayConnection(super_group=[], client=ray_client)
        context["ray"] = ray_connection
        context["enterprise_id"] = None
        ray_client.id_token = "test_token"

        # Mock sufficient tokens (value=1 means we need ceil(1*0.1)=1 token)
        mock_get_client_tokens.return_value = GetCreditBalanceResponse(
            ai_token=10, mt_token=0
        )
        mock_is_ibm.return_value = False

        result = await require_mt_tokens(context, value=1)
        assert result is True
        mock_get_client_tokens.assert_called_once_with("test_token")

    @pytest.mark.asyncio
    @patch("app.slack.middleware.get_group_tokens")
    @patch("app.slack.middleware.is_ibm_enterprise")
    async def test_require_mt_tokens_with_sufficient_group_tokens(
        self, mock_is_ibm, mock_get_group_tokens, context
    ):
        """Test that require_mt_tokens returns True when super_group has sufficient tokens."""
        from app.auth.connector import GetCreditBalanceResponse

        super_group = RaySuperGroup(
            id="sg-123",
            name="Test Group",
            slack_team_id=context["team_id"],
            verify_organization_uuid="org-123",
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=None)
        context["ray"] = ray_connection
        context["enterprise_id"] = None

        # Mock sufficient tokens
        mock_get_group_tokens.return_value = GetCreditBalanceResponse(
            ai_token=10, mt_token=0
        )
        mock_is_ibm.return_value = False

        result = await require_mt_tokens(context, value=1)
        assert result is True
        mock_get_group_tokens.assert_called_once_with("org-123")

    @pytest.mark.asyncio
    @patch("app.slack.middleware.get_client_tokens")
    @patch("app.slack.middleware.get_client_type")
    @patch("app.slack.middleware.is_ibm_enterprise")
    async def test_require_mt_tokens_insufficient_tokens_admin_respond(
        self,
        mock_is_ibm,
        mock_get_client_type,
        mock_get_client_tokens,
        context,
        ray_client,
    ):
        """Test that require_mt_tokens sends message when tokens insufficient (Admin, respond)."""
        from app.auth.connector import GetCreditBalanceResponse

        ray_connection = RayConnection(super_group=[], client=ray_client)
        context["ray"] = ray_connection
        context["enterprise_id"] = None
        ray_client.id_token = "test_token"
        context["response_url"] = "https://hooks.slack.com/test"
        mock_respond = AsyncMock()

        # Mock insufficient tokens
        mock_get_client_tokens.return_value = GetCreditBalanceResponse(
            ai_token=0, mt_token=0
        )
        mock_get_client_type.return_value = "Admin"
        mock_is_ibm.return_value = False

        with patch.object(
            context.__class__,
            "respond",
            new_callable=PropertyMock,
            return_value=mock_respond,
        ):
            result = await require_mt_tokens(context, value=1)
            assert result is False
            mock_respond.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.slack.middleware.get_client_tokens")
    @patch("app.slack.middleware.get_client_type")
    @patch("app.slack.middleware.is_ibm_enterprise")
    async def test_require_mt_tokens_insufficient_tokens_ephemeral(
        self,
        mock_is_ibm,
        mock_get_client_type,
        mock_get_client_tokens,
        context,
        ray_client,
    ):
        """Test that require_mt_tokens sends ephemeral message when respond not available."""
        from app.auth.connector import GetCreditBalanceResponse

        ray_connection = RayConnection(super_group=[], client=ray_client)
        context["ray"] = ray_connection
        context["enterprise_id"] = None
        ray_client.id_token = "test_token"
        context["response_url"] = None
        mock_client = AsyncMock()
        mock_client.chat_postEphemeral = AsyncMock()

        # Mock insufficient tokens
        mock_get_client_tokens.return_value = GetCreditBalanceResponse(
            ai_token=0, mt_token=0
        )
        mock_get_client_type.return_value = "Owner"
        mock_is_ibm.return_value = False

        with (
            patch.object(
                context.__class__,
                "respond",
                new_callable=PropertyMock,
                return_value=None,
            ),
            patch.object(
                context.__class__,
                "client",
                new_callable=PropertyMock,
                return_value=mock_client,
            ),
        ):
            result = await require_mt_tokens(context, value=1)
            assert result is False
            mock_client.chat_postEphemeral.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.slack.middleware.get_client_tokens")
    @patch("app.slack.middleware.get_client_type")
    @patch("app.slack.middleware.is_ibm_enterprise")
    async def test_require_mt_tokens_ibm_enterprise_message(
        self,
        mock_is_ibm,
        mock_get_client_type,
        mock_get_client_tokens,
        context,
        ray_client,
    ):
        """Test that require_mt_tokens uses admin message for IBM enterprise."""
        from app.auth.connector import GetCreditBalanceResponse

        ray_connection = RayConnection(super_group=[], client=ray_client)
        context["ray"] = ray_connection
        context["enterprise_id"] = "E123"
        ray_client.id_token = "test_token"
        context["response_url"] = "https://hooks.slack.com/test"
        mock_respond = AsyncMock()

        # Mock insufficient tokens
        mock_get_client_tokens.return_value = GetCreditBalanceResponse(
            ai_token=0, mt_token=0
        )
        mock_get_client_type.return_value = "Admin"
        mock_is_ibm.return_value = True  # IBM enterprise

        with patch.object(
            context.__class__,
            "respond",
            new_callable=PropertyMock,
            return_value=mock_respond,
        ):
            result = await require_mt_tokens(context, value=1)
            assert result is False
            mock_respond.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.slack.middleware.get_client_tokens")
    @patch("app.slack.middleware.get_client_type")
    @patch("app.slack.middleware.is_ibm_enterprise")
    async def test_require_mt_tokens_value_scaling(
        self,
        mock_is_ibm,
        mock_get_client_type,
        mock_get_client_tokens,
        context,
        ray_client,
    ):
        """Test that require_mt_tokens scales the value correctly (value * 0.1)."""
        from app.auth.connector import GetCreditBalanceResponse

        ray_connection = RayConnection(super_group=[], client=ray_client)
        context["ray"] = ray_connection
        ray_client.id_token = "test_token"
        context["response_url"] = None
        mock_client = AsyncMock()
        mock_client.chat_postEphemeral = AsyncMock()
        mock_get_client_type.return_value = "Admin"
        mock_is_ibm.return_value = False

        # value=10 should require ceil(10*0.1)=1 token
        mock_get_client_tokens.return_value = GetCreditBalanceResponse(
            ai_token=1, mt_token=0
        )

        result = await require_mt_tokens(context, value=10)
        assert result is True

        # value=15 should require ceil(15*0.1)=2 tokens
        mock_get_client_tokens.return_value = GetCreditBalanceResponse(
            ai_token=1, mt_token=0
        )
        with patch.object(
            context.__class__,
            "client",
            new_callable=PropertyMock,
            return_value=mock_client,
        ):
            result = await require_mt_tokens(context, value=15)
            assert result is False  # Only 1 token, need 2
            mock_client.chat_postEphemeral.assert_called_once()
