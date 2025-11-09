"""
Tests for app/slack/listeners.py
"""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from uuid import uuid4

import pytest

from app.auth.connector import RayClient, RayConnection, RayContext, RaySuperGroup
from app.slack.templates.messages import LoginMessage


class TestChannelDeletedEvent:
    """Tests for channel_deleted_event function."""

    @pytest.mark.asyncio
    async def test_channel_deleted_event(self, user_id, team_id):
        """Test channel_deleted_event handler."""
        from app.slack.listeners import channel_deleted_event

        mock_client = AsyncMock()
        event = {"channel": "C123456"}
        context_dict = {"user_id": user_id, "team_id": team_id}

        # The decorator passes context as first arg, then *args to the function
        # Function signature is (client, context, event), so we pass (context_dict, mock_client, event=event)
        with patch(
            "app.slack.listeners.delete_channel_id", new_callable=AsyncMock
        ) as mock_delete:
            await channel_deleted_event(context_dict, mock_client, event=event)
            mock_delete.assert_called_once_with("C123456")


class TestAppUninstalled:
    """Tests for app_uninstalled function."""

    @pytest.mark.asyncio
    async def test_app_uninstalled(self, user_id, team_id):
        """Test app_uninstalled handler."""
        from app.slack.listeners import app_uninstalled

        context_dict = {"user_id": user_id, "team_id": team_id}

        with patch(
            "app.slack.listeners.disconnect_ray_super_group_and_users"
        ) as mock_disconnect:
            await app_uninstalled(context_dict)
            mock_disconnect.assert_called_once_with(team_id, None)


class TestChannelIdChanged:
    """Tests for channel_id_changed function."""

    @pytest.mark.asyncio
    async def test_channel_id_changed(self):
        """Test channel_id_changed handler."""
        from app.slack.listeners import channel_id_changed

        event = {"old_channel_id": "C123", "new_channel_id": "C456"}

        # The decorator passes context as first arg, then *args to the function
        # Function signature is (event), so we pass (context_dict, event=event)
        # But since context is not in the function signature, the decorator injects it as first arg
        context_dict = {}
        with patch(
            "app.slack.listeners.update_channel_id", new_callable=AsyncMock
        ) as mock_update:
            await channel_id_changed(context_dict, event=event)
            mock_update.assert_called_once_with("C123", "C456")


class TestDeleteEphemeralMessage:
    """Tests for delete_ephemeral_message function."""

    @pytest.mark.asyncio
    async def test_delete_ephemeral_message(self):
        """Test delete_ephemeral_message handler."""
        from app.slack.listeners import delete_ephemeral_message

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        # The decorator injects context as first arg when function doesn't have context parameter
        # So we need to pass context dict first
        context_dict = {}

        await delete_ephemeral_message(context_dict, mock_ack, mock_respond)

        mock_ack.assert_called_once()
        mock_respond.assert_called_once_with(delete_original=True)


class TestLink:
    """Tests for link function."""

    @pytest.mark.asyncio
    async def test_link(self):
        """Test link handler."""
        from app.slack.listeners import link

        mock_ack = AsyncMock()
        # The decorator injects context as first arg when function doesn't have context parameter
        # So we need to pass context dict first
        context_dict = {}

        await link(context_dict, mock_ack)

        mock_ack.assert_called_once()


class TestGetAccountInfo:
    """Tests for get_account_info function."""

    @pytest.mark.asyncio
    async def test_get_account_info(self, user_id, team_id, ray_client):
        """Test get_account_info handler."""
        from app.slack.listeners import get_account_info

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()

        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        # The decorator passes context as first arg, then *args to the function
        # Function signature is (ack, context, respond), so we pass (context_dict, mock_ack, respond=mock_respond)
        await get_account_info(context_dict, mock_ack, respond=mock_respond)

        mock_ack.assert_called_once()
        mock_respond.assert_called_once()
        call_args = mock_respond.call_args
        assert "text" in call_args[1]
        assert "blocks" in call_args[1]


class TestGetConnectInfo:
    """Tests for get_connect_info function."""

    @pytest.mark.asyncio
    async def test_get_connect_info(self, user_id, team_id):
        """Test get_connect_info handler."""
        from app.slack.listeners import get_connect_info

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()

        ray_connection = RayConnection(super_group=[], client=None)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        # The decorator passes context as first arg, then *args to the function
        # Function signature is (ack, context, respond), so we pass (context_dict, mock_ack, respond=mock_respond)
        await get_connect_info(context_dict, mock_ack, respond=mock_respond)

        mock_ack.assert_called_once()
        mock_respond.assert_called_once()
        call_args = mock_respond.call_args
        assert "text" in call_args[1]
        assert "blocks" in call_args[1]


class TestGetDelayInfo:
    """Tests for get_delay_info function."""

    @pytest.mark.asyncio
    async def test_get_delay_info(self):
        """Test get_delay_info handler."""
        from app.slack.listeners import get_delay_info

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        # The decorator injects context as first arg when function doesn't have context parameter
        # So we need to pass context dict first
        context_dict = {}

        await get_delay_info(context_dict, mock_ack, mock_respond)

        mock_ack.assert_called_once()
        mock_respond.assert_called_once()


class TestQuote:
    """Tests for quote function."""

    @pytest.mark.asyncio
    async def test_quote(self, user_id, team_id, ray_client):
        """Test quote handler."""
        from app.slack.listeners import quote

        mock_ack = AsyncMock()
        mock_client = AsyncMock()

        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, user_id),
        }

        # The decorator passes context as first arg, then *args to the function
        # Function signature is (ack, context, client), so we pass (context_dict, mock_ack, client=mock_client)
        await quote(context_dict, mock_ack, client=mock_client)

        mock_ack.assert_called_once()
        mock_client.chat_postMessage.assert_called_once()
        call_args = mock_client.chat_postMessage.call_args
        assert "text" in call_args[1]
        assert "blocks" in call_args[1]


class TestDailySummary:
    """Tests for daily_summary function."""

    @pytest.mark.asyncio
    async def test_daily_summary(self, user_id, team_id, ray_client):
        """Test daily_summary handler."""
        from app.slack.listeners import daily_summary

        mock_ack = AsyncMock()
        mock_client = AsyncMock()

        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        # The decorator passes context as first arg, then *args to the function
        # Function signature is (ack, context, client), so we pass (context_dict, mock_ack, client=mock_client)
        with patch(
            "app.slack.listeners.post_job_summary", new_callable=AsyncMock
        ) as mock_post:
            await daily_summary(context_dict, mock_ack, client=mock_client)
            mock_ack.assert_called_once()
            mock_post.assert_called_once()


class TestAllSummary:
    """Tests for all_summary function."""

    @pytest.mark.asyncio
    async def test_all_summary(self, user_id, team_id, ray_client):
        """Test all_summary handler."""
        from app.slack.listeners import all_summary

        mock_ack = AsyncMock()
        mock_client = AsyncMock()

        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        # The decorator passes context as first arg, then *args to the function
        # Function signature is (ack, context, client), so we pass (context_dict, mock_ack, client=mock_client)
        with patch(
            "app.slack.listeners.post_job_summary", new_callable=AsyncMock
        ) as mock_post:
            await all_summary(context_dict, mock_ack, client=mock_client)
            mock_ack.assert_called_once()
            mock_post.assert_called_once()
            assert mock_post.call_args[1]["all_jobs"] is True


class TestHandleAiTranslateHelpAction:
    """Tests for handle_ai_translate_help_action function."""

    @pytest.mark.asyncio
    async def test_handle_ai_translate_help_action(self, user_id, team_id, ray_client):
        """Test handle_ai_translate_help_action handler."""
        from app.slack.listeners import handle_ai_translate_help_action

        mock_ack = AsyncMock()
        mock_client = AsyncMock()

        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        # The decorator passes context as first arg, then *args to the function
        # Function signature is (ack, context, client), so we pass (context_dict, mock_ack, client=mock_client)
        with patch(
            "app.slack.listeners.ai_translate_help", new_callable=AsyncMock
        ) as mock_help:
            await handle_ai_translate_help_action(
                context_dict, mock_ack, client=mock_client
            )
            mock_ack.assert_called_once()
            mock_help.assert_called_once()


class TestHandleVerifyHelpAction:
    """Tests for handle_verify_help_action function."""

    @pytest.mark.asyncio
    async def test_handle_verify_help_action(self, user_id, team_id, ray_client):
        """Test handle_verify_help_action handler."""
        from app.slack.listeners import handle_verify_help_action

        mock_ack = AsyncMock()
        mock_client = AsyncMock()

        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        # The decorator passes context as first arg, then *args to the function
        # Function signature is (ack, context, client), so we pass (context_dict, mock_ack, client=mock_client)
        with patch(
            "app.slack.listeners.verify_help", new_callable=AsyncMock
        ) as mock_help:
            await handle_verify_help_action(context_dict, mock_ack, client=mock_client)
            mock_ack.assert_called_once()
            mock_help.assert_called_once()


class TestHandleHumanHelpAction:
    """Tests for handle_human_help_action function."""

    @pytest.mark.asyncio
    async def test_handle_human_help_action(self, user_id, team_id, ray_client):
        """Test handle_human_help_action handler."""
        from app.slack.listeners import handle_human_help_action

        mock_ack = AsyncMock()
        mock_client = AsyncMock()

        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, user_id),
            "response_url": None,
        }

        # The decorator passes context as first arg, then *args to the function
        # Function signature is (ack, context, client), so we pass (context_dict, mock_ack, client=mock_client)
        await handle_human_help_action(context_dict, mock_ack, client=mock_client)

        mock_ack.assert_called_once()
        mock_client.chat_postMessage.assert_called_once()
        call_args = mock_client.chat_postMessage.call_args
        assert "text" in call_args[1]
        assert "blocks" in call_args[1]


class TestLanguageMtOptionsSelected:
    """Tests for language_mt_options_selected function."""

    @pytest.mark.asyncio
    async def test_language_mt_options_selected(self):
        """Test language_mt_options_selected handler."""
        from app.slack.listeners import language_mt_options_selected

        mock_ack = AsyncMock()
        body = {
            "actions": [
                {
                    "block_id": "file-123",
                    "selected_option": {"value": "en"},
                }
            ]
        }

        with patch(
            "app.slack.listeners.redis_conn.set", new_callable=AsyncMock
        ) as mock_set:
            await language_mt_options_selected(mock_ack, body)
            mock_ack.assert_called_once()
            mock_set.assert_called_once_with("output_file_file-123", "en")


class TestLanguageOptions:
    """Tests for language_options function."""

    @pytest.mark.asyncio
    async def test_language_options(self):
        """Test language_options handler."""
        from app.slack.listeners import language_options

        mock_ack = AsyncMock()
        payload = {"value": "test"}

        with patch(
            "app.slack.listeners.get_language_options", new_callable=AsyncMock
        ) as mock_get_options:
            mock_get_options.return_value = [
                {"text": {"text": "English"}, "value": "en"}
            ]
            await language_options(mock_ack, payload)
            mock_ack.assert_called_once()
            mock_get_options.assert_called_once_with("test")


class TestLanguageOptionsUuid:
    """Tests for language_options_uuid function."""

    @pytest.mark.asyncio
    async def test_language_options_uuid(self):
        """Test language_options_uuid handler."""
        from app.slack.listeners import language_options_uuid

        mock_ack = AsyncMock()
        payload = {"value": "test"}

        with patch(
            "app.slack.listeners.get_language_options", new_callable=AsyncMock
        ) as mock_get_options:
            mock_get_options.return_value = [
                {"text": {"text": "English"}, "value": "uuid-123"}
            ]
            await language_options_uuid(mock_ack, payload)
            mock_ack.assert_called_once()
            mock_get_options.assert_called_once_with("test", "uuid")


class TestGroupOptions:
    """Tests for group_options function."""

    @pytest.mark.asyncio
    async def test_group_options(self, user_id, team_id, ray_client):
        """Test group_options handler."""
        from app.slack.listeners import group_options

        mock_ack = AsyncMock()
        ray_connection = RayConnection(super_group=[], client=ray_client)
        # Create a proper RayContext for the middleware
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "ray": ray_connection,
                "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
            }
        )

        with patch(
            "app.slack.listeners.get_groups", new_callable=AsyncMock
        ) as mock_get_groups:
            mock_get_groups.return_value = [
                {"text": {"text": "Group 1"}, "value": "group-1"}
            ]
            # group_options doesn't have @slack_log_decorator, so we call it directly
            await group_options(mock_ack, context)
            mock_ack.assert_called_once()
            mock_get_groups.assert_called_once_with(ray_client)


class TestFileOptions:
    """Tests for file_options function."""

    @pytest.mark.asyncio
    async def test_file_options(self):
        """Test file_options handler."""
        from app.slack.listeners import file_options

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        payload = {"action_id": "file_options_C123", "value": ""}

        with patch(
            "app.slack.listeners.get_file_options_cached", new_callable=AsyncMock
        ) as mock_get_cached:
            mock_get_cached.return_value = [
                {"text": {"text": "file.txt"}, "value": "F123"}
            ]
            with patch(
                "app.slack.listeners.files_list_simple", new_callable=AsyncMock
            ) as mock_files_list:
                await file_options(mock_ack, payload, mock_client)
                mock_ack.assert_called_once()
                mock_get_cached.assert_called_once_with("C123")


class TestBatchListAction:
    """Tests for batch_list_action function."""

    @pytest.mark.asyncio
    async def test_batch_list_action(self, user_id, team_id, ray_client):
        """Test batch_list_action handler."""
        import json

        from app.slack.listeners import batch_list_action

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        settings = {
            "id": "TJ123",
            "page": 1,
            "page_size": 5,
            "replace_original": False,
        }
        payload = {"value": json.dumps(settings)}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.post_batch_list", new_callable=AsyncMock
        ) as mock_post:
            # The decorator passes context as first arg, then *args to the function
            # Function signature is (ack, payload, context, client), so we pass (context_dict, mock_ack, payload=payload, client=mock_client)
            await batch_list_action(
                context_dict, mock_ack, payload=payload, client=mock_client
            )
            mock_ack.assert_called_once()
            mock_post.assert_called_once()


class TestFileListAction:
    """Tests for file_list_action function."""

    @pytest.mark.asyncio
    async def test_file_list_action(self, user_id, team_id, ray_client):
        """Test file_list_action handler."""
        import json

        from app.slack.listeners import file_list_action

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        settings = {
            "id": "TJ123",
            "page": 1,
            "page_size": 5,
            "replace_original": False,
        }
        payload = {"value": json.dumps(settings)}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.post_file_list", new_callable=AsyncMock
        ) as mock_post:
            # The decorator passes context as first arg, then *args to the function
            # Function signature is (ack, payload, context, client), so we pass (context_dict, mock_ack, payload=payload, client=mock_client)
            await file_list_action(
                context_dict, mock_ack, payload=payload, client=mock_client
            )
            mock_ack.assert_called_once()
            mock_post.assert_called_once()


class TestJobSearchAction:
    """Tests for job_search_action function."""

    @pytest.mark.asyncio
    async def test_job_search_action(self, user_id, team_id, ray_client):
        """Test job_search_action handler."""
        from app.slack.listeners import job_search_action

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = {"trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch("app.slack.listeners.job_search_modal") as mock_modal:
            mock_modal.return_value = {"type": "modal", "title": {"text": "Search Job"}}
            # The decorator passes context as first arg, then *args to the function
            # Function signature is (ack, context, client, body), so we pass (context_dict, mock_ack, client=mock_client, body=body)
            await job_search_action(
                context_dict, mock_ack, client=mock_client, body=body
            )
            mock_ack.assert_called_once()
            mock_client.views_open.assert_called_once()
            assert mock_client.views_open.call_args[1]["trigger_id"] == "trigger-123"


class TestHandleJobSearch:
    """Tests for handle_job_search function."""

    @pytest.mark.asyncio
    async def test_handle_job_search_with_tj_format(self, user_id, team_id, ray_client):
        """Test handle_job_search with TJ format."""
        from app.slack.listeners import handle_job_search

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "state": {
                "values": {
                    "reference": {"reference": {"value": "TJ123"}},
                }
            }
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.post_job_status", new_callable=AsyncMock
        ) as mock_post:
            # The decorator passes context as first arg, then *args to the function
            # Function signature is (ack, view, context, client), so we pass (context_dict, mock_ack, view=view, client=mock_client)
            await handle_job_search(
                context_dict, mock_ack, view=view, client=mock_client
            )
            mock_ack.assert_called_once()
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_job_search_with_numeric_format(
        self, user_id, team_id, ray_client
    ):
        """Test handle_job_search with numeric format."""
        from app.slack.listeners import handle_job_search

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "state": {
                "values": {
                    "reference": {"reference": {"value": "123"}},
                }
            }
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.post_job_status", new_callable=AsyncMock
        ) as mock_post:
            await handle_job_search(
                context_dict, mock_ack, view=view, client=mock_client
            )
            mock_ack.assert_called_once()
            mock_post.assert_called_once()
            # Should prepend "TJ" to the reference
            assert "TJ123" in str(mock_post.call_args)

    @pytest.mark.asyncio
    async def test_handle_job_search_invalid_format(self, user_id, team_id, ray_client):
        """Test handle_job_search with invalid format."""
        from app.slack.listeners import handle_job_search

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "state": {
                "values": {
                    "reference": {"reference": {"value": "invalid"}},
                }
            }
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await handle_job_search(context_dict, mock_ack, view=view, client=mock_client)
        mock_ack.assert_called_once()
        mock_client.chat_postMessage.assert_called_once()
        assert (
            "incorrect format"
            in mock_client.chat_postMessage.call_args[1]["text"].lower()
        )


class TestDisconnectAccountAction:
    """Tests for disconnect_account_action function."""

    @pytest.mark.asyncio
    async def test_disconnect_account_action_with_username(
        self, user_id, team_id, ray_client
    ):
        """Test disconnect_account_action with username."""
        from app.slack.listeners import disconnect_account_action

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        action = {"value": "test.user"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch("app.slack.listeners.disconnect_ray_account") as mock_disconnect:
            # The decorator passes context as first arg, then *args to the function
            # Function signature is (ack, action, context, respond), so we pass (context_dict, mock_ack, action=action, respond=mock_respond)
            await disconnect_account_action(
                context_dict, mock_ack, action=action, respond=mock_respond
            )
            mock_ack.assert_called_once()
            mock_disconnect.assert_called_once()
            mock_respond.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_account_action_without_username(
        self, user_id, team_id, ray_client
    ):
        """Test disconnect_account_action without username."""
        from app.slack.listeners import disconnect_account_action

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        action = None
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch("app.slack.listeners.disconnect_ray_account") as mock_disconnect:
            await disconnect_account_action(
                context_dict, mock_ack, action=action, respond=mock_respond
            )
            mock_ack.assert_called_once()
            mock_disconnect.assert_called_once()
            mock_respond.assert_called_once()


class TestCancelJobAction:
    """Tests for cancel_job_action function."""

    @pytest.mark.asyncio
    async def test_cancel_job_action_with_list_action(
        self, user_id, team_id, ray_client
    ):
        """Test cancel_job_action with list action."""
        import json

        from app.slack.listeners import cancel_job_action

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        payload = {"value": json.dumps({"job_action": "list", "job_id": "TJ123"})}
        body = {"trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.cancel_job_process", new_callable=AsyncMock
        ) as mock_cancel:
            # The decorator passes context as first arg, then *args to the function
            # Function signature is (ack, payload, context, client, body), so we pass (context_dict, mock_ack, payload=payload, client=mock_client, body=body)
            await cancel_job_action(
                context_dict, mock_ack, payload=payload, client=mock_client, body=body
            )
            mock_ack.assert_called_once()
            mock_cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_job_action_with_submit_action(
        self, user_id, team_id, ray_client
    ):
        """Test cancel_job_action with submit action."""
        import json

        from app.slack.listeners import cancel_job_action

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        payload = {
            "value": json.dumps({"job_action": "submit", "job_id": "job-uuid-123"})
        }
        body = {"trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.cancel_job_process", new_callable=AsyncMock
        ) as mock_cancel:
            await cancel_job_action(
                context_dict, mock_ack, payload=payload, client=mock_client, body=body
            )
            mock_ack.assert_called_once()
            mock_cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_job_action_opens_modal(self, user_id, team_id, ray_client):
        """Test cancel_job_action opens modal when no value."""
        from app.slack.listeners import cancel_job_action

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        payload = {}
        body = {"trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch("app.slack.listeners.cancel_job_modal") as mock_modal:
            mock_modal.return_value = {"type": "modal"}
            await cancel_job_action(
                context_dict, mock_ack, payload=payload, client=mock_client, body=body
            )
            mock_ack.assert_called_once()
            mock_client.views_open.assert_called_once()


class TestHandleCancelJob:
    """Tests for handle_cancel_job function."""

    @pytest.mark.asyncio
    async def test_handle_cancel_job_with_tj_format(self, user_id, team_id, ray_client):
        """Test handle_cancel_job with TJ format."""
        from app.slack.listeners import handle_cancel_job

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "state": {
                "values": {
                    "reference": {"reference": {"value": "TJ123"}},
                }
            }
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.cancel_job_process", new_callable=AsyncMock
        ) as mock_cancel:
            # The decorator passes context as first arg, then *args to the function
            # Function signature is (ack, view, context, client), so we pass (context_dict, mock_ack, view=view, client=mock_client)
            await handle_cancel_job(
                context_dict, mock_ack, view=view, client=mock_client
            )
            # handle_cancel_job calls ack() twice - once at the start and once with response_action="clear"
            assert mock_ack.call_count == 2
            mock_cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_cancel_job_invalid_format(self, user_id, team_id, ray_client):
        """Test handle_cancel_job with invalid format."""
        from app.slack.listeners import handle_cancel_job

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "state": {
                "values": {
                    "reference": {"reference": {"value": "invalid"}},
                }
            }
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await handle_cancel_job(context_dict, mock_ack, view=view, client=mock_client)
        # handle_cancel_job calls ack() twice - once at the start and once with response_action="clear"
        assert mock_ack.call_count == 2
        mock_client.chat_postMessage.assert_called_once()
        assert (
            "incorrect format"
            in mock_client.chat_postMessage.call_args[1]["text"].lower()
        )


class TestJobListAction:
    """Tests for job_list_action function."""

    @pytest.mark.asyncio
    async def test_job_list_action_with_selected_option(
        self, user_id, team_id, ray_client
    ):
        """Test job_list_action with selected_option."""
        from app.slack.listeners import job_list_action

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        payload = {"selected_option": {"value": "all"}}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.post_job_list", new_callable=AsyncMock
        ) as mock_post:
            # The decorator passes context as first arg, then *args to the function
            # Function signature is (ack, payload, context, client), so we pass (context_dict, mock_ack, payload=payload, client=mock_client)
            await job_list_action(
                context_dict, mock_ack, payload=payload, client=mock_client
            )
            mock_ack.assert_called_once()
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_job_list_action_with_value(self, user_id, team_id, ray_client):
        """Test job_list_action with value."""
        from app.slack.listeners import job_list_action

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        payload = {"value": "in_progress"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.post_job_list", new_callable=AsyncMock
        ) as mock_post:
            await job_list_action(
                context_dict, mock_ack, payload=payload, client=mock_client
            )
            mock_ack.assert_called_once()
            mock_post.assert_called_once()


class TestJobListPaginatedAction:
    """Tests for job_list_paginated_action function."""

    @pytest.mark.asyncio
    async def test_job_list_paginated_action(self, user_id, team_id, ray_client):
        """Test job_list_paginated_action handler."""
        import json

        from app.slack.listeners import job_list_paginated_action

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        settings = {
            "preset": "all",
            "client_reference": "client-123",
            "page": 1,
            "page_size": 10,
        }
        payload = {"value": json.dumps(settings)}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.post_job_list", new_callable=AsyncMock
        ) as mock_post:
            # The decorator passes context as first arg, then *args to the function
            # Function signature is (ack, payload, context, client), so we pass (context_dict, mock_ack, payload=payload, client=mock_client)
            await job_list_paginated_action(
                context_dict, mock_ack, payload=payload, client=mock_client
            )
            mock_ack.assert_called_once()
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_job_list_paginated_action_invalid_json(
        self, user_id, team_id, ray_client
    ):
        """Test job_list_paginated_action with invalid JSON."""
        from app.slack.listeners import job_list_paginated_action

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        payload = {"value": "invalid json"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.post_job_list", new_callable=AsyncMock
        ) as mock_post:
            await job_list_paginated_action(
                context_dict, mock_ack, payload=payload, client=mock_client
            )
            mock_ack.assert_called_once()
            # Should not call post_job_list if JSON is invalid
            mock_post.assert_not_called()


class TestRayCommand:
    """Tests for ray_command function - the main slash command handler."""

    @pytest.mark.asyncio
    async def test_ray_command_info(self, user_id, team_id, ray_client):
        """Test ray_command with 'info' command."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "info", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        # The decorator passes context as first arg, then *args to the function
        # Function signature is (ack, respond, command, context, client), so we pass (context_dict, mock_ack, respond=mock_respond, command=command, client=mock_client)
        await ray_command(
            context_dict,
            mock_ack,
            respond=mock_respond,
            command=command,
            client=mock_client,
        )
        mock_ack.assert_called_once()
        mock_respond.assert_called_once()
        call_args = mock_respond.call_args
        assert "text" in call_args[1]
        assert "blocks" in call_args[1]

    @pytest.mark.asyncio
    async def test_ray_command_account(self, user_id, team_id, ray_client):
        """Test ray_command with 'account' command."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "account", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await ray_command(
            context_dict,
            mock_ack,
            respond=mock_respond,
            command=command,
            client=mock_client,
        )
        mock_ack.assert_called_once()
        mock_respond.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_command_login(self, user_id, team_id):
        """Test ray_command with 'login' command."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "login", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=None)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await ray_command(
            context_dict,
            mock_ack,
            respond=mock_respond,
            command=command,
            client=mock_client,
        )
        mock_ack.assert_called_once()
        mock_respond.assert_called_once()
        call_args = mock_respond.call_args
        assert "text" in call_args[1]
        assert "blocks" in call_args[1]

    @pytest.mark.asyncio
    async def test_ray_command_logout(self, user_id, team_id, ray_client):
        """Test ray_command with 'logout' command."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "logout", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await ray_command(
            context_dict,
            mock_ack,
            respond=mock_respond,
            command=command,
            client=mock_client,
        )
        mock_ack.assert_called_once()
        mock_respond.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_command_translate_with_settings_enabled(
        self, user_id, team_id, ray_client
    ):
        """Test ray_command with 'translate' command when settings are enabled."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "translate", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch("app.slack.listeners.is_ibm_enterprise", return_value=False):
            with patch(
                "app.slack.listeners.get_auto_translate_settings_and_langs",
                new_callable=AsyncMock,
            ) as mock_get_settings:
                mock_get_settings.return_value = [
                    {"target_lang": "fr", "display_format": "thread"}
                ]
                with patch(
                    "app.slack.listeners.translation_settings_view"
                ) as mock_view:
                    mock_view.return_value = {"type": "modal"}
                    await ray_command(
                        context_dict,
                        mock_ack,
                        respond=mock_respond,
                        command=command,
                        client=mock_client,
                    )
                    mock_ack.assert_called_once()
                    mock_client.views_open.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_command_translate_with_settings_disabled(
        self, user_id, team_id, ray_client
    ):
        """Test ray_command with 'translate' command when settings are disabled."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "translate", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch("app.slack.listeners.is_ibm_enterprise", return_value=True):
            with patch(
                "app.slack.listeners.is_slack_team_admin", new_callable=AsyncMock
            ) as mock_admin:
                mock_admin.return_value = False
                await ray_command(
                    context_dict,
                    mock_ack,
                    respond=mock_respond,
                    command=command,
                    client=mock_client,
                )
                mock_ack.assert_called_once()
                mock_client.chat_postMessage.assert_called_once()
                assert (
                    "help" in mock_client.chat_postMessage.call_args[1]["text"].lower()
                )

    @pytest.mark.asyncio
    async def test_ray_command_job_with_tj_number(self, user_id, team_id, ray_client):
        """Test ray_command with 'job TJ123' command."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "job TJ123", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.post_job_status", new_callable=AsyncMock
        ) as mock_post_status:
            await ray_command(
                context_dict,
                mock_ack,
                respond=mock_respond,
                command=command,
                client=mock_client,
            )
            mock_ack.assert_called_once()
            mock_post_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_command_job_with_client_reference(
        self, user_id, team_id, ray_client
    ):
        """Test ray_command with 'job REF123' command."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "job REF123", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.post_job_list", new_callable=AsyncMock
        ) as mock_post_list:
            await ray_command(
                context_dict,
                mock_ack,
                respond=mock_respond,
                command=command,
                client=mock_client,
            )
            mock_ack.assert_called_once()
            mock_post_list.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_command_jobs(self, user_id, team_id, ray_client):
        """Test ray_command with 'jobs' command."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "jobs", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.post_job_summary", new_callable=AsyncMock
        ) as mock_summary:
            await ray_command(
                context_dict,
                mock_ack,
                respond=mock_respond,
                command=command,
                client=mock_client,
            )
            mock_ack.assert_called_once()
            mock_summary.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_command_my_jobs(self, user_id, team_id, ray_client):
        """Test ray_command with 'my jobs' command."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "my jobs", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.post_job_summary", new_callable=AsyncMock
        ) as mock_summary:
            await ray_command(
                context_dict,
                mock_ack,
                respond=mock_respond,
                command=command,
                client=mock_client,
            )
            mock_ack.assert_called_once()
            mock_summary.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_command_quote(self, user_id, team_id, ray_client):
        """Test ray_command with 'quote' command."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "quote", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await ray_command(
            context_dict,
            mock_ack,
            respond=mock_respond,
            command=command,
            client=mock_client,
        )
        # quote command calls ack() twice - once at start and once in the case
        assert mock_ack.call_count == 2
        mock_respond.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_command_new(self, user_id, team_id, ray_client):
        """Test ray_command with 'new' command."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "new", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await ray_command(
            context_dict,
            mock_ack,
            respond=mock_respond,
            command=command,
            client=mock_client,
        )
        # new command calls ack() twice - once at start and once in the case
        assert mock_ack.call_count == 2
        mock_respond.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_command_help(self, user_id, team_id):
        """Test ray_command with 'help' command."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "help", "trigger_id": "trigger-123"}
        # HelpMessage requires super_group to have at least one element
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=None)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await ray_command(
            context_dict,
            mock_ack,
            respond=mock_respond,
            command=command,
            client=mock_client,
        )
        mock_ack.assert_called_once()
        mock_respond.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_command_empty(self, user_id, team_id):
        """Test ray_command with empty command (defaults to help)."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "", "trigger_id": "trigger-123"}
        # HelpMessage requires super_group to have at least one element
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=None)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await ray_command(
            context_dict,
            mock_ack,
            respond=mock_respond,
            command=command,
            client=mock_client,
        )
        mock_ack.assert_called_once()
        mock_respond.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_command_tj_number(self, user_id, team_id, ray_client):
        """Test ray_command with TJ number as command."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "TJ123", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.post_job_status", new_callable=AsyncMock
        ) as mock_post_status:
            await ray_command(
                context_dict,
                mock_ack,
                respond=mock_respond,
                command=command,
                client=mock_client,
            )
            mock_ack.assert_called_once()
            mock_post_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_command_invalid_single(self, user_id, team_id):
        """Test ray_command with invalid single command."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "invalid", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=None)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await ray_command(
            context_dict,
            mock_ack,
            respond=mock_respond,
            command=command,
            client=mock_client,
        )
        mock_ack.assert_called_once()
        mock_respond.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_command_invalid_multiple(self, user_id, team_id):
        """Test ray_command with invalid multiple word command."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        command = {"text": "invalid command here", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=None)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await ray_command(
            context_dict,
            mock_ack,
            respond=mock_respond,
            command=command,
            client=mock_client,
        )
        mock_ack.assert_called_once()
        mock_respond.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_command_strip_formatting(self, user_id, team_id, ray_client):
        """Test ray_command strips formatting from command text."""
        from app.slack.listeners import ray_command

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        # Test with bold formatting
        command = {"text": "*info*", "trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await ray_command(
            context_dict,
            mock_ack,
            respond=mock_respond,
            command=command,
            client=mock_client,
        )
        mock_ack.assert_called_once()
        mock_respond.assert_called_once()
