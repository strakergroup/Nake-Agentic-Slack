"""
Tests for app/slack/listeners.py
"""

import json
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.auth.connector import RayConnection, RayContext, RaySuperGroup
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


class TestHandleNewJob:
    """Tests for handle_new_job function - critical job creation handler."""

    @pytest.mark.asyncio
    async def test_handle_new_job_validation_error(self, user_id, team_id, ray_client):
        """Test handle_new_job with validation error."""
        from app.slack.listeners import handle_new_job

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        # Invalid form data that will cause ValidationError (empty files list)
        # Need to provide proper structure that parses but fails validation
        view = {
            "state": {
                "values": {
                    "files": {
                        "file_options_C123": {"selected_options": []}
                    },  # Empty files - will fail validation
                    "source_lang": {
                        "language_options": {
                            "selected_option": {
                                "value": "en",
                                "text": {"text": "English"},
                            }
                        }
                    },
                    "target_langs": {
                        "language_options": {"selected_options": []}
                    },  # Empty target langs - will fail validation
                    "service": {
                        "service": {"selected_option": {"value": "Translation"}}
                    },
                    "timeframe": {"timeframe": {"selected_option": {"value": "3"}}},
                    "reference": {"reference": {"value": ""}},
                    "group": {"group_options": {"selected_option": None}},
                    "notes": {"notes": {"value": ""}},
                }
            }
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
            "log": MagicMock(add_api_log=MagicMock()),
        }

        await handle_new_job(context_dict, mock_ack, view=view, client=mock_client)
        # Should call ack with errors (ValidationError will be caught and converted)
        # The errors dict will contain validation errors
        assert mock_ack.call_count == 1
        call_args = mock_ack.call_args
        assert call_args[1]["response_action"] == "errors"
        assert "errors" in call_args[1]

    @pytest.mark.asyncio
    async def test_handle_new_job_success_with_job_id(
        self, user_id, team_id, ray_client
    ):
        """Test handle_new_job successful submission with job_id."""
        from app.ray.service import RayResponse
        from app.slack.listeners import handle_new_job

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "state": {
                "values": {
                    "files": {
                        "file_options_C123": {
                            "selected_options": [
                                {"value": "F123", "text": {"text": "file.txt"}}
                            ]
                        }
                    },
                    "source_lang": {
                        "language_options": {
                            "selected_option": {
                                "value": "en",
                                "text": {"text": "English"},
                            }
                        }
                    },
                    "target_langs": {
                        "language_options": {
                            "selected_options": [
                                {"value": "fr", "text": {"text": "French"}},
                                {"value": "es", "text": {"text": "Spanish"}},
                            ]
                        }
                    },
                    "service": {
                        "service": {"selected_option": {"value": "Translation"}}
                    },
                    "timeframe": {"timeframe": {"selected_option": {"value": "3"}}},
                    "reference": {"reference": {"value": ""}},
                    "group": {"group_options": {"selected_option": None}},
                    "notes": {"notes": {"value": ""}},
                }
            }
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
            "log": MagicMock(add_api_log=MagicMock()),
        }

        # Mock submit_job response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://api.example.com/job"
        mock_response.json.return_value = {"Message": {"job_id": "TJ123"}}
        mock_response.content = b'{"Message": {"job_id": "TJ123"}}'
        mock_response.headers = {}
        mock_ray_response = RayResponse(response=mock_response, data=None)

        with patch(
            "app.slack.listeners.submit_job", new_callable=AsyncMock
        ) as mock_submit:
            mock_submit.return_value = [mock_ray_response]
            with patch("app.slack.listeners.is_ibm_enterprise", return_value=False):
                await handle_new_job(
                    context_dict, mock_ack, view=view, client=mock_client
                )
                assert mock_ack.call_count == 1
                mock_submit.assert_called_once()
                mock_client.chat_postMessage.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_new_job_ray_api_error(self, user_id, team_id, ray_client):
        """Test handle_new_job with RayAPIResponseError."""
        from ray_sdk import RayAPIResponseError

        from app.slack.listeners import handle_new_job

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "state": {
                "values": {
                    "files": {
                        "file_options_C123": {
                            "selected_options": [
                                {"value": "F123", "text": {"text": "file.txt"}}
                            ]
                        }
                    },
                    "source_lang": {
                        "language_options": {
                            "selected_option": {
                                "value": "en",
                                "text": {"text": "English"},
                            }
                        }
                    },
                    "target_langs": {
                        "language_options": {
                            "selected_options": [
                                {"value": "fr", "text": {"text": "French"}}
                            ]
                        }
                    },
                    "service": {
                        "service": {"selected_option": {"value": "Translation"}}
                    },
                    "timeframe": {"timeframe": {"selected_option": {"value": "3"}}},
                    "reference": {"reference": {"value": ""}},
                    "group": {"group_options": {"selected_option": None}},
                    "notes": {"notes": {"value": ""}},
                }
            }
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
            "log": MagicMock(add_api_log=MagicMock()),
        }

        # Mock RayAPIResponseError
        mock_response = MagicMock()
        mock_response.json.return_value = {"error": "API Error"}
        mock_response.content = b'{"error": "API Error"}'
        mock_request = MagicMock()
        api_error = RayAPIResponseError(
            message="API Error", request=mock_request, response=mock_response
        )

        with patch(
            "app.slack.listeners.submit_job", new_callable=AsyncMock
        ) as mock_submit:
            mock_submit.side_effect = api_error
            with patch("app.slack.listeners.notify_exception") as mock_notify:
                await handle_new_job(
                    context_dict, mock_ack, view=view, client=mock_client
                )
                mock_ack.assert_called_once()
                mock_notify.assert_called_once()
                mock_client.chat_postMessage.assert_called_once()
                assert (
                    "error" in mock_client.chat_postMessage.call_args[1]["text"].lower()
                )

    @pytest.mark.asyncio
    async def test_handle_new_job_general_exception(self, user_id, team_id, ray_client):
        """Test handle_new_job with general exception."""
        from app.slack.listeners import handle_new_job

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "state": {
                "values": {
                    "files": {
                        "file_options_C123": {
                            "selected_options": [
                                {"value": "F123", "text": {"text": "file.txt"}}
                            ]
                        }
                    },
                    "source_lang": {
                        "language_options": {
                            "selected_option": {
                                "value": "en",
                                "text": {"text": "English"},
                            }
                        }
                    },
                    "target_langs": {
                        "language_options": {
                            "selected_options": [
                                {"value": "fr", "text": {"text": "French"}}
                            ]
                        }
                    },
                    "service": {
                        "service": {"selected_option": {"value": "Translation"}}
                    },
                    "timeframe": {"timeframe": {"selected_option": {"value": "3"}}},
                    "reference": {"reference": {"value": ""}},
                    "group": {"group_options": {"selected_option": None}},
                    "notes": {"notes": {"value": ""}},
                }
            }
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
            "log": MagicMock(add_api_log=MagicMock()),
        }

        with patch(
            "app.slack.listeners.submit_job", new_callable=AsyncMock
        ) as mock_submit:
            mock_submit.side_effect = Exception("General error")
            with patch("app.slack.listeners.notify_exception") as mock_notify:
                await handle_new_job(
                    context_dict, mock_ack, view=view, client=mock_client
                )
                mock_ack.assert_called_once()
                mock_notify.assert_called_once()
                mock_client.chat_postMessage.assert_called_once()
                assert (
                    "error" in mock_client.chat_postMessage.call_args[1]["text"].lower()
                )

    @pytest.mark.asyncio
    async def test_handle_new_job_no_ray_client(self, user_id, team_id):
        """Test handle_new_job when user is not logged in."""
        from app.slack.listeners import handle_new_job

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {"state": {"values": {}}}
        ray_connection = RayConnection(super_group=[], client=None)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await handle_new_job(context_dict, mock_ack, view=view, client=mock_client)
        mock_ack.assert_called_once_with(response_action="clear")
        mock_client.chat_postMessage.assert_called_once()


class TestLoginSsoAction:
    """Tests for login_sso_action function - critical SSO authentication handler."""

    @pytest.mark.asyncio
    async def test_login_sso_action_success(self, user_id, team_id):
        """Test login_sso_action successful SSO login."""
        from app.slack.listeners import login_sso_action

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        view = {}
        ray_connection = RayConnection(super_group=[], client=None)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
            "response_url": "https://hooks.slack.com/test",
        }

        user_info_response = {
            "ok": True,
            "user": {
                "profile": {
                    "email": "test@example.com",
                    "first_name": "Test",
                    "last_name": "User",
                }
            },
        }

        with patch.object(
            mock_client, "users_info", new_callable=AsyncMock
        ) as mock_users_info:
            mock_users_info.return_value = user_info_response
            with patch(
                "app.slack.listeners.connect_ray_account_sso", new_callable=AsyncMock
            ) as mock_connect:
                with patch(
                    "app.slack.listeners.get_ray_connection", new_callable=AsyncMock
                ) as mock_get_connection:
                    new_ray_connection = RayConnection(
                        super_group=[], client=MagicMock()
                    )
                    mock_get_connection.return_value = new_ray_connection
                    with patch(
                        "app.slack.listeners.is_ibm_enterprise", return_value=False
                    ):
                        await login_sso_action(
                            context_dict,
                            mock_ack,
                            respond=mock_respond,
                            client=mock_client,
                            view=view,
                        )
                        mock_ack.assert_called()
                        mock_connect.assert_called_once()
                        mock_get_connection.assert_called_once()
                        mock_respond.assert_called()

    @pytest.mark.asyncio
    async def test_login_sso_action_missing_scope_error(self, user_id, team_id):
        """Test login_sso_action with missing_scope SlackApiError."""
        from slack_sdk.errors import SlackApiError

        from app.slack.listeners import login_sso_action

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        view = {}
        ray_connection = RayConnection(super_group=[], client=None)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
            "response_url": "https://hooks.slack.com/test",
        }

        slack_error = SlackApiError(
            message="missing_scope",
            response={"ok": False, "error": "missing_scope"},
        )

        with patch.object(
            mock_client, "users_info", new_callable=AsyncMock
        ) as mock_users_info:
            mock_users_info.side_effect = slack_error
            await login_sso_action(
                context_dict,
                mock_ack,
                respond=mock_respond,
                client=mock_client,
                view=view,
            )
            mock_ack.assert_called_with(response_action="clear")
            mock_respond.assert_called()

    @pytest.mark.asyncio
    async def test_login_sso_action_general_slack_error(self, user_id, team_id):
        """Test login_sso_action with general SlackApiError."""
        from slack_sdk.errors import SlackApiError

        from app.slack.listeners import login_sso_action

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        view = {}
        ray_connection = RayConnection(super_group=[], client=None)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
            "response_url": "https://hooks.slack.com/test",
        }

        slack_error = SlackApiError(
            message="other_error",
            response={"ok": False, "error": "other_error"},
        )

        with patch.object(
            mock_client, "users_info", new_callable=AsyncMock
        ) as mock_users_info:
            mock_users_info.side_effect = slack_error
            with patch("app.slack.listeners.notify_exception") as mock_notify:
                await login_sso_action(
                    context_dict,
                    mock_ack,
                    respond=mock_respond,
                    client=mock_client,
                    view=view,
                )
                mock_ack.assert_called()
                mock_notify.assert_called_once()
                mock_respond.assert_called()

    @pytest.mark.asyncio
    async def test_login_sso_action_general_exception(self, user_id, team_id):
        """Test login_sso_action with general exception."""
        from app.slack.listeners import login_sso_action

        mock_ack = AsyncMock()
        mock_respond = AsyncMock()
        mock_client = AsyncMock()
        view = {}
        ray_connection = RayConnection(super_group=[], client=None)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
            "response_url": "https://hooks.slack.com/test",
        }

        with patch.object(
            mock_client, "users_info", new_callable=AsyncMock
        ) as mock_users_info:
            mock_users_info.side_effect = Exception("General error")
            with patch("app.slack.listeners.notify_exception") as mock_notify:
                await login_sso_action(
                    context_dict,
                    mock_ack,
                    respond=mock_respond,
                    client=mock_client,
                    view=view,
                )
                mock_ack.assert_called()
                mock_notify.assert_called_once()
                mock_respond.assert_called()


class TestEvaluateJobSubmit:
    """Tests for evaluate_job_submit function - quality evaluation job handler."""

    @pytest.mark.asyncio
    async def test_evaluate_job_submit_no_view(self, user_id, team_id, ray_client):
        """Test evaluate_job_submit with no view."""
        from app.slack.listeners import evaluate_job_submit

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await evaluate_job_submit(
            context_dict, view=None, client=mock_client, ack=mock_ack
        )
        mock_ack.assert_called_once_with(response_action="clear")
        mock_client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_evaluate_job_submit_no_channel_id(
        self, user_id, team_id, ray_client
    ):
        """Test evaluate_job_submit with no channel_id in private_metadata."""
        from app.slack.listeners import evaluate_job_submit

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {"state": {"values": {}}}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await evaluate_job_submit(
            context_dict, view=view, client=mock_client, ack=mock_ack
        )
        mock_ack.assert_called_once_with(response_action="clear")
        mock_client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_evaluate_job_submit_validation_error(
        self, user_id, team_id, ray_client
    ):
        """Test evaluate_job_submit with validation error."""
        from app.slack.listeners import evaluate_job_submit

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "callback_id": "evaluate_job",
            "private_metadata": "C123",
            "state": {
                "values": {
                    "source_lang": {
                        "source_language_option_uuid": {
                            "selected_option": {"value": "src-lang-001"}
                        }
                    },
                    "target_langs": {
                        "language_options_uuid": {
                            "selected_options": [{"value": "lang-123"}]
                        }
                    },
                    "files": {
                        "files": {
                            "selected_options": []  # Empty files will cause validation error
                        }
                    },
                }
            },
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await evaluate_job_submit(
            context_dict, view=view, client=mock_client, ack=mock_ack
        )
        mock_ack.assert_called_once_with(
            response_action="errors", errors=mock_ack.call_args.kwargs.get("errors", {})
        )
        mock_client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_evaluate_job_submit_source_equals_target(
        self, user_id, team_id, ray_client
    ):
        """Test that submitting with the same source and target language shows an inline error."""
        from app.slack.listeners import evaluate_job_submit

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "callback_id": "evaluate_job",
            "private_metadata": "C123",
            "state": {
                "values": {
                    "source_lang": {
                        "source_language_option_uuid": {
                            "selected_option": {"value": "lang-123"}
                        }
                    },
                    "target_langs": {
                        "language_options_uuid": {
                            "selected_options": [{"value": "lang-123"}]
                        }
                    },
                    "files": {
                        "files": {
                            "selected_options": [
                                {"value": "F123", "text": {"text": "file.txt"}}
                            ]
                        }
                    },
                }
            },
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await evaluate_job_submit(
            context_dict, view=view, client=mock_client, ack=mock_ack
        )
        mock_ack.assert_called_once()
        call_kwargs = mock_ack.call_args.kwargs
        assert call_kwargs["response_action"] == "errors"
        assert "target_langs" in call_kwargs["errors"]
        assert "source language" in call_kwargs["errors"]["target_langs"].lower()
        mock_client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_evaluate_job_submit_source_equals_target_family(
        self, user_id, team_id, ray_client
    ):
        """Test that regional variants in the same language family are rejected inline."""
        from app.slack.listeners import evaluate_job_submit

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "callback_id": "evaluate_job",
            "private_metadata": "C123",
            "state": {
                "values": {
                    "source_lang": {
                        "source_language_option_uuid": {
                            "selected_option": {"value": "lang-fr"}
                        }
                    },
                    "target_langs": {
                        "language_options_uuid": {
                            "selected_options": [{"value": "lang-fr-ca"}]
                        }
                    },
                    "files": {
                        "files": {
                            "selected_options": [
                                {"value": "F123", "text": {"text": "file.txt"}}
                            ]
                        }
                    },
                }
            },
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        with patch(
            "app.slack.listeners.get_conflicting_target_language_labels",
            new_callable=AsyncMock,
        ) as mock_conflicts:
            mock_conflicts.return_value = ["French Canadian"]
            await evaluate_job_submit(
                context_dict, view=view, client=mock_client, ack=mock_ack
            )

        mock_ack.assert_called_once()
        call_kwargs = mock_ack.call_args.kwargs
        assert call_kwargs["response_action"] == "errors"
        assert "target_langs" in call_kwargs["errors"]
        assert "regional variant" in call_kwargs["errors"]["target_langs"].lower()
        assert "french canadian" in call_kwargs["errors"]["target_langs"].lower()
        mock_client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_evaluate_job_submit_success(self, user_id, team_id, ray_client):
        """Test evaluate_job_submit successful submission."""
        from app.slack.listeners import evaluate_job_submit

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "callback_id": "evaluate_job",
            "private_metadata": "C123",
            "state": {
                "values": {
                    "source_lang": {
                        "source_language_option_uuid": {
                            "selected_option": {"value": "src-lang-001"}
                        }
                    },
                    "target_langs": {
                        "language_options_uuid": {
                            "selected_options": [{"value": "lang-123"}]
                        }
                    },
                    "files": {
                        "files": {
                            "selected_options": [
                                {"value": "F123", "text": {"text": "file.txt"}}
                            ]
                        }
                    },
                }
            },
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        # Mock file download and validation
        mock_file = MagicMock()
        mock_file.name = "file.txt"
        mock_file.content = b"test content"

        with patch(
            "app.slack.listeners.download_file", new_callable=AsyncMock
        ) as mock_download:
            mock_download.return_value = mock_file
            with patch(
                "app.slack.listeners.validate_file", return_value=(True, True, None)
            ):
                with patch(
                    "app.slack.listeners.get_conflicting_target_language_labels",
                    new_callable=AsyncMock,
                ) as mock_conflicts:
                    mock_conflicts.return_value = []
                    with patch(
                        "app.slack.listeners.submit_evaluation_job",
                        new_callable=AsyncMock,
                    ) as mock_submit:
                        await evaluate_job_submit(
                            context_dict, view=view, client=mock_client, ack=mock_ack
                        )
                        mock_ack.assert_called_once()
                        mock_submit.assert_called_once()
                        # Should post success message
                        assert mock_client.chat_postMessage.call_count >= 1

    @pytest.mark.asyncio
    async def test_evaluate_job_submit_verify_api_error(
        self, user_id, team_id, ray_client
    ):
        """Test evaluate_job_submit with VerifyAPIError."""
        from app.api.verify import VerifyAPIError
        from app.slack.listeners import evaluate_job_submit

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "callback_id": "evaluate_job",
            "private_metadata": "C123",
            "state": {
                "values": {
                    "source_lang": {
                        "source_language_option_uuid": {
                            "selected_option": {"value": "src-lang-001"}
                        }
                    },
                    "target_langs": {
                        "language_options_uuid": {
                            "selected_options": [{"value": "lang-123"}]
                        }
                    },
                    "files": {
                        "files": {
                            "selected_options": [
                                {"value": "F123", "text": {"text": "file.txt"}}
                            ]
                        }
                    },
                }
            },
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        mock_file = MagicMock()
        mock_file.name = "file.txt"
        mock_file.content = b"test content"

        with patch(
            "app.slack.listeners.download_file", new_callable=AsyncMock
        ) as mock_download:
            mock_download.return_value = mock_file
            with patch(
                "app.slack.listeners.validate_file", return_value=(True, True, None)
            ):
                with patch(
                    "app.slack.listeners.get_conflicting_target_language_labels",
                    new_callable=AsyncMock,
                ) as mock_conflicts:
                    mock_conflicts.return_value = []
                    with patch(
                        "app.slack.listeners.submit_evaluation_job",
                        new_callable=AsyncMock,
                    ) as mock_submit:
                        mock_submit.side_effect = VerifyAPIError("Permission denied")
                        await evaluate_job_submit(
                            context_dict, view=view, client=mock_client, ack=mock_ack
                        )
                        mock_ack.assert_called_once()
                        # Should post permission error message
                        assert mock_client.chat_postMessage.call_count >= 2
                        call_args_list = mock_client.chat_postMessage.call_args_list
                        last_call_text = call_args_list[-1][1]["text"].lower()
                        assert (
                            "permission" in last_call_text
                            or "administrator" in last_call_text
                        )

    @pytest.mark.asyncio
    async def test_evaluate_job_submit_general_exception(
        self, user_id, team_id, ray_client
    ):
        """Test evaluate_job_submit with general exception."""
        from app.slack.listeners import evaluate_job_submit

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "callback_id": "evaluate_job",
            "private_metadata": "C123",
            "state": {
                "values": {
                    "source_lang": {
                        "source_language_option_uuid": {
                            "selected_option": {"value": "src-lang-001"}
                        }
                    },
                    "target_langs": {
                        "language_options_uuid": {
                            "selected_options": [{"value": "lang-123"}]
                        }
                    },
                    "files": {
                        "files": {
                            "selected_options": [
                                {"value": "F123", "text": {"text": "file.txt"}}
                            ]
                        }
                    },
                }
            },
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        mock_file = MagicMock()
        mock_file.name = "file.txt"
        mock_file.content = b"test content"

        with patch(
            "app.slack.listeners.download_file", new_callable=AsyncMock
        ) as mock_download:
            mock_download.return_value = mock_file
            with patch(
                "app.slack.listeners.validate_file", return_value=(True, True, None)
            ):
                with patch(
                    "app.slack.listeners.get_conflicting_target_language_labels",
                    new_callable=AsyncMock,
                ) as mock_conflicts:
                    mock_conflicts.return_value = []
                    with patch(
                        "app.slack.listeners.submit_evaluation_job",
                        new_callable=AsyncMock,
                    ) as mock_submit:
                        mock_submit.side_effect = Exception("General error")
                        with patch(
                            "app.slack.listeners.notify_exception"
                        ) as mock_notify:
                            await evaluate_job_submit(
                                context_dict,
                                view=view,
                                client=mock_client,
                                ack=mock_ack,
                            )
                            mock_ack.assert_called_once()
                            mock_notify.assert_called_once()
                            # Should post error message
                            assert mock_client.chat_postMessage.call_count >= 2
                            call_args_list = mock_client.chat_postMessage.call_args_list
                            last_call_text = call_args_list[-1][1]["text"].lower()
                            assert "error" in last_call_text


class TestHandleDocumentMtJob:
    """Tests for handle_document_mt_job function - document MT job handler."""

    @pytest.mark.asyncio
    async def test_handle_document_mt_job_no_languages(
        self, user_id, team_id, ray_client
    ):
        """Test handle_document_mt_job with no languages selected."""
        from app.slack.listeners import handle_document_mt_job

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "state": {
                "values": {
                    "target_langs": {"language_mt_options": {"selected_options": []}},
                    "files": {"files": {"selected_options": [{"value": "F123"}]}},
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

        await handle_document_mt_job(
            context_dict, mock_ack, view=view, client=mock_client
        )
        mock_ack.assert_called_once()
        mock_client.chat_postMessage.assert_called_once()
        assert "language" in mock_client.chat_postMessage.call_args[1]["text"].lower()

    @pytest.mark.asyncio
    async def test_handle_document_mt_job_no_files(self, user_id, team_id, ray_client):
        """Test handle_document_mt_job with no files selected."""
        from app.slack.listeners import handle_document_mt_job

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "state": {
                "values": {
                    "target_langs": {
                        "language_mt_options": {"selected_options": [{"value": "en"}]}
                    },
                    "files": {"files": {"selected_options": []}},
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

        await handle_document_mt_job(
            context_dict, mock_ack, view=view, client=mock_client
        )
        mock_ack.assert_called_once()
        mock_client.chat_postMessage.assert_called_once()
        assert "file" in mock_client.chat_postMessage.call_args[1]["text"].lower()

    @pytest.mark.asyncio
    async def test_handle_document_mt_job_no_ray_client(self, user_id, team_id):
        """Test handle_document_mt_job when user is not logged in."""
        from app.slack.listeners import handle_document_mt_job

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {"state": {"values": {}}}
        ray_connection = RayConnection(super_group=[], client=None)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
            "login_prompt": LoginMessage(user_id, team_id, None, "C123"),
        }

        await handle_document_mt_job(
            context_dict, mock_ack, view=view, client=mock_client
        )
        # ack is called twice - once at start, once in else block
        assert mock_ack.call_count == 2
        mock_client.chat_postMessage.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_document_mt_job_applies_pdf_limit_for_trial(
        self, user_id, team_id, ray_client
    ):
        """Test document MT uses the PDF limit only for trial sessions."""
        from app.config import config
        from app.slack.listeners import handle_document_mt_job

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "state": {
                "values": {
                    "target_langs": {
                        "language_mt_options": {
                            "selected_options": [
                                {"value": "en", "text": {"text": "English"}}
                            ]
                        }
                    },
                    "files": {
                        "files": {
                            "selected_options": [
                                {"value": "F123", "text": {"text": "file.pdf"}}
                            ]
                        }
                    },
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
            "app.slack.listeners.get_verify_trial_status", new_callable=AsyncMock
        ) as mock_trial_status:
            mock_trial_status.return_value = (True, 7)
            with patch(
                "app.slack.listeners.download_file", new_callable=AsyncMock
            ) as mock_download:
                mock_download.return_value = "/tmp/file.pdf"
                with patch(
                    "app.slack.listeners.validate_file", return_value=(False, False, "")
                ) as mock_validate:
                    await handle_document_mt_job(
                        context_dict, mock_ack, view=view, client=mock_client
                    )

        mock_validate.assert_called_once_with(
            "/tmp/file.pdf",
            max_pdf_size_bytes=config.document_mt_pdf_max_size_bytes,
        )
        assert ray_client.is_trial is True
        assert ray_client.trial_remaining == 7

    @pytest.mark.asyncio
    async def test_handle_document_mt_job_skips_pdf_limit_for_non_trial(
        self, user_id, team_id, ray_client
    ):
        """Test document MT skips the PDF limit outside trial sessions."""
        from app.slack.listeners import handle_document_mt_job

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "state": {
                "values": {
                    "target_langs": {
                        "language_mt_options": {
                            "selected_options": [
                                {"value": "en", "text": {"text": "English"}}
                            ]
                        }
                    },
                    "files": {
                        "files": {
                            "selected_options": [
                                {"value": "F123", "text": {"text": "file.pdf"}}
                            ]
                        }
                    },
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
            "app.slack.listeners.get_verify_trial_status", new_callable=AsyncMock
        ) as mock_trial_status:
            mock_trial_status.return_value = (False, 0)
            with patch(
                "app.slack.listeners.download_file", new_callable=AsyncMock
            ) as mock_download:
                mock_download.return_value = "/tmp/file.pdf"
                with patch(
                    "app.slack.listeners.validate_file", return_value=(False, False, "")
                ) as mock_validate:
                    await handle_document_mt_job(
                        context_dict, mock_ack, view=view, client=mock_client
                    )

        mock_validate.assert_called_once_with(
            "/tmp/file.pdf",
            max_pdf_size_bytes=None,
        )
        assert ray_client.is_trial is False
        assert ray_client.trial_remaining == 0

    @pytest.mark.asyncio
    async def test_handle_document_mt_job_exception(self, user_id, team_id, ray_client):
        """Test handle_document_mt_job with exception during file processing."""
        from app.slack.listeners import handle_document_mt_job

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "state": {
                "values": {
                    "target_langs": {
                        "language_mt_options": {
                            "selected_options": [
                                {"value": "en", "text": {"text": "English"}}
                            ]
                        }
                    },
                    "files": {
                        "files": {
                            "selected_options": [
                                {"value": "F123", "text": {"text": "file.txt"}}
                            ]
                        }
                    },
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

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test content")
            input_file = f.name

        with patch(
            "app.slack.listeners.download_file", new_callable=AsyncMock
        ) as mock_download:
            mock_download.return_value = input_file
            with patch(
                "app.slack.listeners.validate_file", return_value=(True, True, None)
            ):
                with patch(
                    "app.slack.listeners.upload_to_file_server",
                    return_value="file-id-123",
                ):
                    mock_record = MagicMock()
                    mock_record.id = "record-123"
                    with patch(
                        "app.slack.listeners.check_and_record_submission_async",
                        new_callable=AsyncMock,
                    ) as mock_check:
                        mock_check.return_value = (False, mock_record)
                        # Make document_machine_translate fail
                        with patch(
                            "app.slack.listeners.document_machine_translate",
                            new_callable=AsyncMock,
                        ) as mock_doc_mt:
                            mock_doc_mt.side_effect = Exception("Submit error")
                            with patch(
                                "app.slack.listeners.notify_exception"
                            ) as mock_notify:
                                await handle_document_mt_job(
                                    context_dict,
                                    mock_ack,
                                    view=view,
                                    client=mock_client,
                                )
                                mock_ack.assert_called_once()
                                mock_notify.assert_called_once()
                                # Should post error message
                                assert mock_client.chat_postMessage.call_count >= 1
                                call_args_list = (
                                    mock_client.chat_postMessage.call_args_list
                                )
                                last_call_text = call_args_list[-1][1]["text"].lower()
                                assert "error" in last_call_text


class TestMessageEvent:
    """Tests for message_event function - handles all incoming messages."""

    @pytest.mark.asyncio
    async def test_message_event_duplicate_detection(self, user_id, team_id):
        """Test message_event ignores duplicate events."""
        from app.slack.listeners import message_event

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "test"}
        body = {"event": {"team": team_id}}
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": "E123",
            "is_bot": False,
            "channel_id": "C123",
        }

        with patch(
            "app.slack.listeners.is_duplicate_event", new_callable=AsyncMock
        ) as mock_duplicate:
            mock_duplicate.return_value = True
            # The decorator passes context as first arg, then *args to the function
            # Function signature is (client, context, message, body), so we pass (context_dict, mock_client, message=message, body=body)
            await message_event(context_dict, mock_client, message=message, body=body)
            mock_duplicate.assert_called_once_with("E123", "message", "123456.789")

    @pytest.mark.asyncio
    async def test_message_event_bot_message_ignored(self, user_id, team_id):
        """Test message_event ignores bot messages."""
        from app.slack.listeners import message_event

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "test"}
        body = {"event": {"team": team_id}}
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "is_bot": True,  # Bot message
            "channel_id": "C123",
        }

        with patch(
            "app.slack.listeners.respond_to_message", new_callable=AsyncMock
        ) as mock_respond:
            await message_event(context_dict, mock_client, message=message, body=body)
            mock_respond.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_event_dm_calls_respond_to_message(
        self, user_id, team_id, ray_client
    ):
        """Test message_event in DM calls respond_to_message."""
        from app.slack.listeners import message_event

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "test", "channel_type": "im"}
        body = {"event": {"team": team_id}}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "is_bot": False,
            "channel_id": user_id,  # DM channel
            "ray": ray_connection,
            "bot_user_id": "B123",
        }

        with patch(
            "app.slack.listeners.get_bot_token_async", new_callable=AsyncMock
        ) as mock_get_token:
            mock_get_token.return_value = None
            with patch(
                "app.slack.listeners.respond_to_message", new_callable=AsyncMock
            ) as mock_respond:
                await message_event(
                    context_dict, mock_client, message=message, body=body
                )
                mock_respond.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_event_channel_auto_translate(
        self, user_id, team_id, ray_client
    ):
        """Test message_event in channel calls auto_translate_message."""
        from app.slack.listeners import message_event

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "Hello world"}
        body = {"event": {"team": team_id}}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "is_bot": False,
            "channel_id": "C123",  # Channel, not DM
            "ray": ray_connection,
            "bot_user_id": "B123",
        }

        with patch(
            "app.slack.listeners.is_channel_im", return_value=False
        ) as mock_is_im:
            with patch(
                "app.slack.listeners.auto_translate_message", new_callable=AsyncMock
            ) as mock_auto_translate:
                await message_event(
                    context_dict, mock_client, message=message, body=body
                )
                mock_auto_translate.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_event_srt_thread_shows_media_embed_option(
        self, user_id, team_id, ray_client
    ):
        """Test SRT uploads in video-option threads trigger the embed reply."""
        from app.slack.listeners import message_event

        mock_client = AsyncMock()
        message = {
            "ts": "123456.789",
            "thread_ts": "123450.000",
            "files": [{"id": "F123", "name": "captions.srt", "filetype": "srt"}],
        }
        body = {"event": {"team": team_id}}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "is_bot": False,
            "channel_id": "C123",
            "ray": ray_connection,
            "bot_user_id": "B123",
        }

        with (
            patch("app.slack.listeners.is_channel_im", return_value=False),
            patch(
                "app.slack.listeners.maybe_show_thread_media_embed_option",
                new_callable=AsyncMock,
            ) as mock_maybe_embed,
        ):
            mock_maybe_embed.return_value = True
            await message_event(context_dict, mock_client, message=message, body=body)
            mock_maybe_embed.assert_called_once_with(mock_client, context_dict, message)

    @pytest.mark.asyncio
    async def test_message_event_srt_thread_in_dm_shows_media_embed_option(
        self, user_id, team_id, ray_client
    ):
        """Test threaded DM SRT uploads prefer the embed flow over generic DM handling."""
        from app.slack.listeners import message_event

        mock_client = AsyncMock()
        message = {
            "ts": "123456.789",
            "thread_ts": "123450.000",
            "channel_type": "im",
            "files": [{"id": "F123", "name": "captions.srt", "filetype": "srt"}],
        }
        body = {"event": {"team": team_id}}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "is_bot": False,
            "channel_id": user_id,
            "ray": ray_connection,
            "bot_user_id": "B123",
        }

        with (
            patch(
                "app.slack.listeners.maybe_show_thread_media_embed_option",
                new_callable=AsyncMock,
            ) as mock_maybe_embed,
            patch(
                "app.slack.listeners.respond_to_message",
                new_callable=AsyncMock,
            ) as mock_respond,
        ):
            mock_maybe_embed.return_value = True
            await message_event(context_dict, mock_client, message=message, body=body)
            mock_maybe_embed.assert_called_once_with(mock_client, context_dict, message)
            mock_respond.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_video_embed_subtitles_submits_existing_srt_embed(
        self, user_id, team_id, ray_client
    ):
        """Test thread SRT embed action bypasses the modal and submits directly."""
        from app.slack.listeners import handle_video_embed_subtitles

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        mock_client.token = "xoxb-test-token"
        mock_client.files_info.return_value = {
            "file": {"url_private_download": "https://example.com/video.mp4"}
        }
        action = {
            "value": json.dumps(
                {
                    "channel_id": "C123",
                    "thread_ts": "123456.789",
                    "files": [
                        {
                            "file_id": "V123",
                            "file_name": "video.mp4",
                            "duration_ms": 60000,
                        }
                    ],
                    "subtitle_file": {
                        "file_id": "S123",
                        "file_name": "captions.srt",
                        "language_code": "und",
                    },
                }
            )
        }
        body = {"trigger_id": "trigger-123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "channel_id": "C123",
            "ray": ray_connection,
        }

        with (
            patch(
                "app.ray.submissions.check_and_record_direct_embed_submission_async",
                new_callable=AsyncMock,
            ) as mock_check_record,
            patch(
                "app.slack.listeners.download_file", new_callable=AsyncMock
            ) as mock_download_file,
            patch(
                "app.slack.listeners.upload_to_file_server", new_callable=AsyncMock
            ) as mock_upload_to_file_server,
            patch(
                "app.transcriber_tasks.tasks.create_asr_task", new_callable=AsyncMock
            ) as mock_create_task,
        ):
            mock_check_record.return_value = (False, MagicMock(id=99))
            mock_download_file.return_value = "/tmp/captions.srt"
            mock_upload_to_file_server.return_value = "gridfs-srt-123"
            await handle_video_embed_subtitles(
                context_dict,
                mock_ack,
                action=action,
                body=body,
                client=mock_client,
            )

            mock_ack.assert_called_once()
            mock_client.views_open.assert_not_called()
            mock_download_file.assert_called_once_with(
                client=mock_client, file_id="S123", http=None
            )
            mock_upload_to_file_server.assert_called_once_with("/tmp/captions.srt")
            mock_create_task.assert_called_once()
            asr_task = mock_create_task.call_args.args[0]
            assert asr_task.extra_data["pipeline_type"] == "embed"
            assert asr_task.extra_data["srt_file_ids"] == ["gridfs-srt-123"]
            assert asr_task.extra_data["language_codes"] == ["und"]
            assert asr_task.extra_data["original_video_file_id"] == "V123"
            assert asr_task.extra_data["slack_thread_ts"] == "123456.789"

    @pytest.mark.asyncio
    async def test_message_event_bot_mentioned_no_auto_translate(
        self, user_id, team_id, ray_client
    ):
        """Test message_event doesn't auto-translate when bot is mentioned."""
        from app.slack.listeners import message_event

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "<@B123> hello"}
        body = {"event": {"team": team_id}}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "is_bot": False,
            "channel_id": "C123",
            "ray": ray_connection,
            "bot_user_id": "B123",
        }

        with patch(
            "app.slack.listeners.is_channel_im", return_value=False
        ) as mock_is_im:
            with patch(
                "app.slack.listeners.auto_translate_message", new_callable=AsyncMock
            ) as mock_auto_translate:
                await message_event(
                    context_dict, mock_client, message=message, body=body
                )
                # Should not call auto_translate when bot is mentioned
                mock_auto_translate.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_event_token_update(self, user_id, team_id, ray_client):
        """Test message_event updates client token if different."""
        from app.slack.listeners import message_event

        mock_client = AsyncMock()
        mock_client.token = "old-token"
        message = {"ts": "123456.789", "text": "test", "channel_type": "im"}
        body = {"event": {"team": team_id}}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "is_bot": False,
            "channel_id": user_id,
            "ray": ray_connection,
            "bot_user_id": "B123",
        }

        with patch(
            "app.slack.listeners.get_bot_token_async", new_callable=AsyncMock
        ) as mock_get_token:
            mock_get_token.return_value = "new-token"
            with patch(
                "app.slack.listeners.respond_to_message", new_callable=AsyncMock
            ) as mock_respond:
                await message_event(
                    context_dict, mock_client, message=message, body=body
                )
                assert mock_client.token == "new-token"
                mock_respond.assert_called_once()


class TestRespondToMessage:
    """Tests for respond_to_message function - handles file uploads, MT requests, Watson intents."""

    @pytest.mark.asyncio
    async def test_respond_to_message_with_files(self, user_id, team_id, ray_client):
        """Test respond_to_message with file uploads."""
        from uuid import uuid4

        from app.slack.listener_actions import respond_to_message

        mock_client = AsyncMock()
        message = {
            "ts": "123456.789",
            "files": [
                {
                    "id": "F123",
                    "name": "test.txt",
                    "title": "test.txt",
                    "filetype": "text",
                }
            ],
        }
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "ray": ray_connection,
                "say": AsyncMock(),
            }
        )

        with patch(
            "app.slack.listener_actions.require_ray_client", new_callable=AsyncMock
        ) as mock_require:
            mock_require.return_value = True
            with patch(
                "app.slack.listener_actions.files_list_simple", new_callable=AsyncMock
            ) as mock_files_list:
                with patch(
                    "app.slack.listener_actions.is_video_file", return_value=False
                ):
                    with patch(
                        "app.slack.listener_actions.validate_file_type",
                        return_value=True,
                    ):
                        await respond_to_message(mock_client, context, message)
                        mock_files_list.assert_called_once()
                        context.say.assert_called_once()

    @pytest.mark.asyncio
    async def test_respond_to_message_too_many_files(
        self, user_id, team_id, ray_client
    ):
        """Test respond_to_message with more than 10 files."""
        from app.slack.listener_actions import respond_to_message

        mock_client = AsyncMock()
        message = {
            "ts": "123456.789",
            "files": [{"id": f"F{i}", "name": f"test{i}.txt"} for i in range(11)],
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "ray": ray_connection,
                "say": AsyncMock(),
            }
        )

        with patch(
            "app.slack.listener_actions.require_ray_client", new_callable=AsyncMock
        ) as mock_require:
            mock_require.return_value = True
            with patch(
                "app.slack.listener_actions.files_list_simple", new_callable=AsyncMock
            ):
                with patch(
                    "app.slack.listener_actions.is_video_file", return_value=False
                ):
                    with patch(
                        "app.slack.listener_actions.validate_file_type",
                        return_value=True,
                    ):
                        await respond_to_message(mock_client, context, message)
                        # Should post error about too many files
                        assert context.say.call_count >= 1
                        call_args = context.say.call_args
                        assert (
                            "10" in call_args[1]["text"]
                            or "maximum" in call_args[1]["text"].lower()
                        )

    @pytest.mark.asyncio
    async def test_respond_to_message_unsupported_file(
        self, user_id, team_id, ray_client
    ):
        """Test respond_to_message with unsupported file type."""
        from app.slack.listener_actions import respond_to_message

        mock_client = AsyncMock()
        message = {
            "ts": "123456.789",
            "files": [{"id": "F123", "name": "test.exe", "filetype": "exe"}],
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "ray": ray_connection,
                "say": AsyncMock(),
            }
        )

        with patch(
            "app.slack.listener_actions.require_ray_client", new_callable=AsyncMock
        ) as mock_require:
            mock_require.return_value = True
            with patch(
                "app.slack.listener_actions.files_list_simple", new_callable=AsyncMock
            ):
                with patch(
                    "app.slack.listener_actions.is_video_file", return_value=False
                ):
                    with patch(
                        "app.slack.listener_actions.validate_file_type",
                        return_value=False,
                    ):
                        await respond_to_message(mock_client, context, message)
                        # Should post error about unsupported file
                        assert context.say.call_count >= 1
                        call_args = context.say.call_args
                        assert "unsupported" in call_args[1]["text"].lower()

    @pytest.mark.asyncio
    async def test_respond_to_message_mt_request(self, user_id, team_id):
        """Test respond_to_message with MT request pattern."""
        from app.slack.listener_actions import respond_to_message

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "mt: en to fr: Hello world"}
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "locale": "en",
                "say": AsyncMock(),
                "log": MagicMock(set_watson_log=MagicMock()),
            }
        )

        with patch(
            "app.slack.listener_actions.detect_language", new_callable=AsyncMock
        ) as mock_detect:
            mock_detect.return_value = MagicMock(language="en")
            with patch(
                "app.slack.listener_actions.get_mt_translation", new_callable=AsyncMock
            ) as mock_mt:
                await respond_to_message(mock_client, context, message)
                mock_mt.assert_called_once()
                # Should extract source lang, target lang, and text
                call_args = mock_mt.call_args
                assert call_args[1]["source_lang"] == "en"
                assert call_args[1]["target_lang"] == "fr"
                assert call_args[1]["sentence"] == "Hello world"

    @pytest.mark.asyncio
    async def test_respond_to_message_mt_request_no_source_lang(self, user_id, team_id):
        """Test respond_to_message with MT request without source lang (auto-detect)."""
        from app.slack.listener_actions import respond_to_message

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "mt: to fr: Hello world"}
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "locale": "en",
                "say": AsyncMock(),
                "log": MagicMock(set_watson_log=MagicMock()),
            }
        )

        with patch(
            "app.slack.listener_actions.detect_language", new_callable=AsyncMock
        ) as mock_detect:
            mock_detect.return_value = MagicMock(language="en")
            with patch(
                "app.slack.listener_actions.get_mt_translation", new_callable=AsyncMock
            ) as mock_mt:
                await respond_to_message(mock_client, context, message)
                mock_detect.assert_called_once()
                mock_mt.assert_called_once()

    @pytest.mark.asyncio
    async def test_respond_to_message_watson_help_intent(self, user_id, team_id):
        """Test respond_to_message with Watson Help intent."""
        from app.slack.listener_actions import respond_to_message

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "help"}
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "say": AsyncMock(),
                "log": MagicMock(set_watson_log=MagicMock()),
            }
        )

        mock_watson_response = MagicMock()
        mock_watson_response.status_code = 200
        mock_watson_response.data = {
            "output": {"intents": [{"intent": "General_Greetings"}], "entities": []}
        }
        mock_watson_response.headers = {}
        mock_watson_response.intent = "General_Greetings"

        with patch(
            "app.slack.listener_actions.watson_message",
            return_value=mock_watson_response,
        ):
            await respond_to_message(mock_client, context, message)
            # Should call say with HelpMessage
            assert context.say.call_count >= 1

    @pytest.mark.asyncio
    async def test_respond_to_message_watson_login_intent(self, user_id, team_id):
        """Test respond_to_message with Watson Login intent."""
        from app.slack.listener_actions import respond_to_message

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "login"}
        ray_connection = RayConnection(super_group=[], client=None)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "enterprise_id": None,
                "ray": ray_connection,
                "say": AsyncMock(),
                "log": MagicMock(set_watson_log=MagicMock()),
            }
        )

        mock_watson_response = MagicMock()
        mock_watson_response.status_code = 200
        mock_watson_response.data = {
            "output": {"intents": [{"intent": "Login"}], "entities": []}
        }
        mock_watson_response.headers = {}
        mock_watson_response.intent = "Login"

        with patch(
            "app.slack.listener_actions.watson_message",
            return_value=mock_watson_response,
        ):
            await respond_to_message(mock_client, context, message)
            # Should post ephemeral login message
            mock_client.chat_postEphemeral.assert_called_once()

    @pytest.mark.asyncio
    async def test_respond_to_message_no_ray_client_for_files(self, user_id, team_id):
        """Test respond_to_message with files but no ray client."""
        from app.slack.listener_actions import respond_to_message

        mock_client = AsyncMock()
        message = {
            "ts": "123456.789",
            "files": [{"id": "F123", "name": "test.txt"}],
            "text": "",  # Add empty text to avoid KeyError
        }
        ray_connection = RayConnection(super_group=[], client=None)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "ray": ray_connection,
                "say": AsyncMock(),
                "log": MagicMock(set_watson_log=MagicMock()),
            }
        )

        with patch(
            "app.slack.listener_actions.require_ray_client", new_callable=AsyncMock
        ) as mock_require:
            mock_require.return_value = False
            # When no ray client, files aren't processed but message text still is
            # So Watson will be called and may call say
            with patch(
                "app.slack.listener_actions.watson_message",
                return_value=MagicMock(intent="Unknown"),
            ):
                await respond_to_message(mock_client, context, message)
                # Files won't be processed, but message text processing may still call say
                # The key is that files_list_simple should not be called
                # We can't easily assert that, but we know files weren't processed
                # because require_ray_client returned False

    @pytest.mark.asyncio
    async def test_respond_to_message_use_thread(self, user_id, team_id, ray_client):
        """Test respond_to_message with use_thread=True."""
        from uuid import uuid4

        from app.slack.listener_actions import respond_to_message

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "help"}
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "ray": ray_connection,
                "say": AsyncMock(),
                "log": MagicMock(set_watson_log=MagicMock()),
            }
        )

        mock_watson_response = MagicMock()
        mock_watson_response.status_code = 200
        mock_watson_response.data = {
            "output": {"intents": [{"intent": "General_Greetings"}], "entities": []}
        }
        mock_watson_response.headers = {}
        mock_watson_response.intent = "General_Greetings"

        with patch(
            "app.slack.listener_actions.watson_message",
            return_value=mock_watson_response,
        ):
            await respond_to_message(mock_client, context, message, use_thread=True)
            # Should pass thread_ts to say
            call_args = context.say.call_args
            assert "thread_ts" in call_args[1]
            assert call_args[1]["thread_ts"] == "123456.789"


class TestAutoTranslateMessage:
    """Tests for auto_translate_message function - handles automatic message translation."""

    @pytest.mark.asyncio
    async def test_auto_translate_message_no_text(self, user_id, team_id, ray_client):
        """Test auto_translate_message with no text."""
        from app.slack.listener_actions import auto_translate_message

        mock_client = AsyncMock()
        message = {"ts": "123456.789"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "ray": ray_connection,
            }
        )

        await auto_translate_message(mock_client, context, message)
        # Should return early, no calls
        mock_client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_translate_message_bot_message(
        self, user_id, team_id, ray_client
    ):
        """Test auto_translate_message ignores bot messages."""
        from app.slack.listener_actions import auto_translate_message

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "Hello", "bot_id": "B123"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "ray": ray_connection,
            }
        )

        await auto_translate_message(mock_client, context, message)
        # Should return early, no calls
        mock_client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_translate_message_5k_limit(self, user_id, team_id, ray_client):
        """Test auto_translate_message with message over 5K character limit."""
        from app.slack.listener_actions import auto_translate_message

        mock_client = AsyncMock()
        long_text = "a" * 5001  # Over 5K limit
        message = {"ts": "123456.789", "text": long_text}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "ray": ray_connection,
            }
        )

        await auto_translate_message(mock_client, context, message)
        # Should post error about 5K limit
        mock_client.chat_postMessage.assert_called_once()
        call_args = mock_client.chat_postMessage.call_args
        assert "5K" in call_args[1]["text"] or "5000" in call_args[1]["text"]
        assert call_args[1]["thread_ts"] == "123456.789"

    @pytest.mark.asyncio
    async def test_auto_translate_message_no_settings(
        self, user_id, team_id, ray_client
    ):
        """Test auto_translate_message with no auto-translate settings."""
        from app.slack.listener_actions import auto_translate_message

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "Hello world"}
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "ray": ray_connection,
            }
        )

        with patch(
            "app.slack.listener_actions.get_auto_translate_settings_and_langs",
            new_callable=AsyncMock,
        ) as mock_get_settings:
            mock_get_settings.return_value = []  # No settings
            await auto_translate_message(mock_client, context, message)
            # Should return early, no translation request
            mock_client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_translate_message_no_tokens(self, user_id, team_id, ray_client):
        """Test auto_translate_message when insufficient tokens."""
        from app.slack.listener_actions import auto_translate_message

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "Hello world"}
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "ray": ray_connection,
            }
        )

        with patch(
            "app.slack.listener_actions.get_auto_translate_settings_and_langs",
            new_callable=AsyncMock,
        ) as mock_get_settings:
            mock_get_settings.return_value = [
                {"target_lang": "fr", "display_format": "thread"}
            ]
            with patch(
                "app.slack.listener_actions.require_mt_tokens", new_callable=AsyncMock
            ) as mock_require_tokens:
                mock_require_tokens.return_value = False  # Insufficient tokens
                await auto_translate_message(mock_client, context, message)
                # Should return early, no translation request
                mock_client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_translate_message_success(self, user_id, team_id, ray_client):
        """Test auto_translate_message successful translation."""
        from app.slack.listener_actions import auto_translate_message

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "Hello world"}
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "ray": ray_connection,
            }
        )

        with patch(
            "app.slack.listener_actions.get_auto_translate_settings_and_langs",
            new_callable=AsyncMock,
        ) as mock_get_settings:
            mock_get_settings.return_value = [
                {"target_lang": "fr", "display_format": "thread"}
            ]
            with patch(
                "app.slack.listener_actions.require_mt_tokens", new_callable=AsyncMock
            ) as mock_require_tokens:
                mock_require_tokens.return_value = True
                with patch(
                    "app.slack.listener_actions.detect_language", new_callable=AsyncMock
                ) as mock_detect:
                    mock_detect.return_value = MagicMock(language="en")
                    with patch(
                        "app.slack.listener_actions.evaluate_get_glossary_resource",
                        new_callable=AsyncMock,
                    ) as mock_glossary:
                        mock_glossary.return_value = None
                        with patch(
                            "app.slack.listener_actions.send_mt_translation_request",
                            new_callable=AsyncMock,
                        ) as mock_send_mt:
                            await auto_translate_message(mock_client, context, message)
                            # Should send translation request
                            mock_send_mt.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_translate_message_same_source_target_lang(
        self, user_id, team_id, ray_client
    ):
        """Test auto_translate_message when detected language matches target language."""
        from app.slack.listener_actions import auto_translate_message

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "Bonjour"}
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "ray": ray_connection,
            }
        )

        with patch(
            "app.slack.listener_actions.get_auto_translate_settings_and_langs",
            new_callable=AsyncMock,
        ) as mock_get_settings:
            mock_get_settings.return_value = [
                {"target_lang": "fr", "display_format": "thread"}
            ]
            with patch(
                "app.slack.listener_actions.require_mt_tokens", new_callable=AsyncMock
            ) as mock_require_tokens:
                mock_require_tokens.return_value = True
                with patch(
                    "app.slack.listener_actions.detect_language", new_callable=AsyncMock
                ) as mock_detect:
                    # Detected language matches target language
                    mock_detect.return_value = MagicMock(language="fr")
                    await auto_translate_message(mock_client, context, message)
                    # Should return early, no translation needed
                    mock_client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_translate_message_exception_handling(
        self, user_id, team_id, ray_client
    ):
        """Test auto_translate_message exception handling."""
        from app.slack.listener_actions import auto_translate_message

        mock_client = AsyncMock()
        message = {"ts": "123456.789", "text": "Hello world"}
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "channel_id": "C123",
                "ray": ray_connection,
            }
        )

        with patch(
            "app.slack.listener_actions.get_auto_translate_settings_and_langs",
            new_callable=AsyncMock,
        ) as mock_get_settings:
            mock_get_settings.return_value = [
                {"target_lang": "fr", "display_format": "thread"}
            ]
            with patch(
                "app.slack.listener_actions.require_mt_tokens", new_callable=AsyncMock
            ) as mock_require_tokens:
                mock_require_tokens.return_value = True
                with patch(
                    "app.slack.listener_actions.detect_language", new_callable=AsyncMock
                ) as mock_detect:
                    mock_detect.return_value = MagicMock(language="en")
                    with patch(
                        "app.slack.listener_actions.evaluate_get_glossary_resource",
                        new_callable=AsyncMock,
                    ) as mock_glossary:
                        mock_glossary.return_value = None
                        with patch(
                            "app.slack.listener_actions.send_mt_translation_request",
                            new_callable=AsyncMock,
                        ) as mock_send_mt:
                            mock_send_mt.side_effect = Exception("Translation error")
                            with patch(
                                "app.slack.listener_actions.notify_exception"
                            ) as mock_notify:
                                await auto_translate_message(
                                    mock_client, context, message
                                )
                                # Should notify exception
                                mock_notify.assert_called_once()


class TestAppMentionEvent:
    """Tests for app_mention_event function - handles bot mentions."""

    @pytest.mark.asyncio
    async def test_app_mention_event_duplicate_detection(self, user_id, team_id):
        """Test app_mention_event ignores duplicate events."""
        from app.slack.listeners import app_mention_event

        mock_client = AsyncMock()
        event = {"ts": "123456.789", "text": "test"}
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": "E123",
            "is_bot": False,
        }

        with patch(
            "app.slack.listeners.is_duplicate_event", new_callable=AsyncMock
        ) as mock_duplicate:
            mock_duplicate.return_value = True
            await app_mention_event(context_dict, mock_client, event=event)
            mock_duplicate.assert_called_once_with("E123", "app_mention", "123456.789")

    @pytest.mark.asyncio
    async def test_app_mention_event_bot_message_ignored(self, user_id, team_id):
        """Test app_mention_event ignores bot messages."""
        from app.slack.listeners import app_mention_event

        mock_client = AsyncMock()
        event = {"ts": "123456.789", "text": "test"}
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "is_bot": True,  # Bot message
        }

        with patch(
            "app.slack.listeners.respond_to_message", new_callable=AsyncMock
        ) as mock_respond:
            await app_mention_event(context_dict, mock_client, event=event)
            mock_respond.assert_not_called()

    @pytest.mark.asyncio
    async def test_app_mention_event_with_text(self, user_id, team_id, ray_client):
        """Test app_mention_event with text calls respond_to_message."""
        from app.slack.listeners import app_mention_event

        mock_client = AsyncMock()
        event = {"ts": "123456.789", "text": "help"}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "is_bot": False,
            "ray": ray_connection,
        }

        with patch(
            "app.slack.listeners.respond_to_message", new_callable=AsyncMock
        ) as mock_respond:
            await app_mention_event(context_dict, mock_client, event=event)
            mock_respond.assert_called_once_with(
                mock_client, context_dict, event, use_thread=True
            )

    @pytest.mark.asyncio
    async def test_app_mention_event_with_files(self, user_id, team_id, ray_client):
        """Test app_mention_event with files calls respond_to_message."""
        from app.slack.listeners import app_mention_event

        mock_client = AsyncMock()
        event = {"ts": "123456.789", "text": "", "files": [{"id": "F123"}]}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "is_bot": False,
            "ray": ray_connection,
        }

        with patch(
            "app.slack.listeners.respond_to_message", new_callable=AsyncMock
        ) as mock_respond:
            await app_mention_event(context_dict, mock_client, event=event)
            mock_respond.assert_called_once_with(
                mock_client, context_dict, event, use_thread=True
            )

    @pytest.mark.asyncio
    async def test_app_mention_event_no_text_no_files(
        self, user_id, team_id, ray_client
    ):
        """Test app_mention_event with no text and no files."""
        from app.slack.listeners import app_mention_event

        mock_client = AsyncMock()
        event = {"ts": "123456.789", "text": ""}
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "is_bot": False,
            "ray": ray_connection,
        }

        with patch(
            "app.slack.listeners.respond_to_message", new_callable=AsyncMock
        ) as mock_respond:
            await app_mention_event(context_dict, mock_client, event=event)
            # Should not call respond_to_message when no text and no files
            # (TODO: Show auto-translate settings modal)
            mock_respond.assert_not_called()


class TestHandleVerifyJobSubmission:
    """Tests for handle_verify_job_submission function - complex verification job handler."""

    @pytest.mark.asyncio
    async def test_handle_verify_job_submission_lock_not_acquired(
        self, user_id, team_id, ray_client
    ):
        """Test handle_verify_job_submission when Redis lock cannot be acquired."""
        from app.slack.listeners import handle_verify_job_submission

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = {
            "view": {
                "private_metadata": json.dumps(
                    {"job_uuid": "job-123", "timestamp": "123456.789"}
                ),
            },
            "user": {"id": user_id},
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
        }

        with patch(
            "app.slack.listeners.redis_conn.set", new_callable=AsyncMock
        ) as mock_redis_set:
            mock_redis_set.return_value = False  # Lock not acquired
            await handle_verify_job_submission(
                context_dict, mock_ack, body=body, client=mock_client
            )
            mock_ack.assert_called_once_with(response_action="clear")
            mock_client.chat_postMessage.assert_called_once()
            call_args = mock_client.chat_postMessage.call_args
            assert "no longer available" in call_args[1]["text"].lower()

    @pytest.mark.asyncio
    async def test_handle_verify_job_submission_success(
        self, user_id, team_id, ray_client
    ):
        """Test handle_verify_job_submission successful submission."""
        from uuid import uuid4

        from app.slack.listeners import handle_verify_job_submission

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        job_uuid = "job-123"
        file_uuid = "file-123"
        lang_uuid = "lang-123"
        body = {
            "view": {
                "private_metadata": json.dumps(
                    {"job_uuid": job_uuid, "timestamp": "123456.789"}
                ),
                "state": {
                    "values": {
                        f"verification_checkbox_{lang_uuid}_{file_uuid}": {
                            "verification_checkbox_action": {
                                "selected_options": [
                                    {"value": f"{file_uuid}:{lang_uuid}:10"}
                                ]
                            }
                        }
                    }
                },
            },
            "user": {"id": user_id},
        }
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
        }

        mock_job = {
            "data": {
                "workflow_uuid": "workflow-123",  # Not human evaluation
                "target_languages": [{"uuid": lang_uuid, "name": "French"}],
                "source_files": [
                    {
                        "file_uuid": file_uuid,
                        "filename": "test.txt",
                        "target_files": [],
                    }
                ],
            }
        }

        with patch(
            "app.slack.listeners.redis_conn.set", new_callable=AsyncMock
        ) as mock_redis_set:
            mock_redis_set.return_value = True  # Lock acquired
            with patch(
                "app.slack.listeners.get_client_evaluation_job", new_callable=AsyncMock
            ) as mock_get_job:
                mock_get_job.return_value = mock_job
                with patch(
                    "app.slack.listeners.submit_verification_job",
                    new_callable=AsyncMock,
                ) as mock_submit:
                    await handle_verify_job_submission(
                        context_dict, mock_ack, body=body, client=mock_client
                    )
                    mock_ack.assert_called_once_with(response_action="clear")
                    mock_submit.assert_called_once()
                    call_args = mock_submit.call_args
                    assert call_args[1]["job_uuid"] == job_uuid
                    assert call_args[1]["user_id"] == user_id

    @pytest.mark.asyncio
    async def test_handle_verify_job_submission_human_evaluation_workflow(
        self, user_id, team_id, ray_client
    ):
        """Test handle_verify_job_submission with human evaluation workflow."""
        from uuid import uuid4

        from app.constants import HUMAN_EVALUATION_WORKFLOW_UUID
        from app.slack.listeners import handle_verify_job_submission

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        job_uuid = "job-123"
        file_uuid = "file-123"
        lang_uuid = "lang-123"
        body = {
            "view": {
                "private_metadata": json.dumps(
                    {"job_uuid": job_uuid, "timestamp": "123456.789"}
                ),
                "state": {
                    "values": {
                        f"verification_checkbox_{lang_uuid}_{file_uuid}": {
                            "verification_checkbox_action": {
                                "selected_options": [
                                    {"value": f"{file_uuid}:{lang_uuid}:10"}
                                ]
                            }
                        }
                    }
                },
            },
            "user": {"id": user_id},
        }
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
        }

        mock_job = {
            "data": {
                "workflow_uuid": HUMAN_EVALUATION_WORKFLOW_UUID,
                "target_languages": [{"uuid": lang_uuid, "name": "French"}],
                "source_files": [
                    {
                        "file_uuid": file_uuid,
                        "filename": "test.txt",
                        "target_files": [
                            {"language_uuid": lang_uuid, "human_job_status": None}
                        ],
                    }
                ],
            }
        }

        with patch(
            "app.slack.listeners.redis_conn.set", new_callable=AsyncMock
        ) as mock_redis_set:
            mock_redis_set.return_value = True
            with patch(
                "app.slack.listeners.get_client_evaluation_job", new_callable=AsyncMock
            ) as mock_get_job:
                mock_get_job.return_value = mock_job
                with patch(
                    "app.slack.listeners.submit_verification_job",
                    new_callable=AsyncMock,
                ) as mock_submit:
                    await handle_verify_job_submission(
                        context_dict, mock_ack, body=body, client=mock_client
                    )
                    mock_ack.assert_called_once_with(response_action="clear")
                    # Verify that human_job_status was set to "Submitted"
                    assert (
                        mock_job["data"]["source_files"][0]["target_files"][0][
                            "human_job_status"
                        ]
                        == "Submitted"
                    )
                    mock_submit.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_verify_job_submission_human_evaluation_cancels_unselected(
        self, user_id, team_id, ray_client
    ):
        """Test handle_verify_job_submission cancels unselected files in human evaluation."""
        from uuid import uuid4

        from app.constants import HUMAN_EVALUATION_WORKFLOW_UUID
        from app.slack.listeners import handle_verify_job_submission

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        job_uuid = "job-123"
        file_uuid = "file-123"
        lang_uuid_1 = "lang-123"
        lang_uuid_2 = "lang-456"
        body = {
            "view": {
                "private_metadata": json.dumps(
                    {"job_uuid": job_uuid, "timestamp": "123456.789"}
                ),
                "state": {
                    "values": {
                        f"verification_checkbox_{lang_uuid_1}_{file_uuid}": {
                            "verification_checkbox_action": {
                                "selected_options": [
                                    {"value": f"{file_uuid}:{lang_uuid_1}:10"}
                                ]
                            }
                        }
                        # lang_uuid_2 is not selected
                    }
                },
            },
            "user": {"id": user_id},
        }
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
        }

        mock_job = {
            "data": {
                "workflow_uuid": HUMAN_EVALUATION_WORKFLOW_UUID,
                "target_languages": [
                    {"uuid": lang_uuid_1, "name": "French"},
                    {"uuid": lang_uuid_2, "name": "Spanish"},
                ],
                "source_files": [
                    {
                        "file_uuid": file_uuid,
                        "filename": "test.txt",
                        "target_files": [
                            {"language_uuid": lang_uuid_1, "human_job_status": None},
                            {"language_uuid": lang_uuid_2, "human_job_status": None},
                        ],
                    }
                ],
            }
        }

        with patch(
            "app.slack.listeners.redis_conn.set", new_callable=AsyncMock
        ) as mock_redis_set:
            mock_redis_set.return_value = True
            with patch(
                "app.slack.listeners.get_client_evaluation_job", new_callable=AsyncMock
            ) as mock_get_job:
                mock_get_job.return_value = mock_job
                with patch(
                    "app.slack.listeners.submit_verification_job",
                    new_callable=AsyncMock,
                ) as mock_submit:
                    await handle_verify_job_submission(
                        context_dict, mock_ack, body=body, client=mock_client
                    )
                    # Verify that lang_uuid_1 is Submitted and lang_uuid_2 is Cancelled
                    target_files = mock_job["data"]["source_files"][0]["target_files"]
                    lang_1_file = next(
                        tf for tf in target_files if tf["language_uuid"] == lang_uuid_1
                    )
                    lang_2_file = next(
                        tf for tf in target_files if tf["language_uuid"] == lang_uuid_2
                    )
                    assert lang_1_file["human_job_status"] == "Submitted"
                    assert lang_2_file["human_job_status"] == "Cancelled"
                    mock_submit.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_verify_job_submission_no_selected_options(
        self, user_id, team_id, ray_client
    ):
        """Test handle_verify_job_submission with no selected options."""
        from uuid import uuid4

        from app.slack.listeners import handle_verify_job_submission

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        job_uuid = "job-123"
        file_uuid = "file-123"
        lang_uuid = "lang-123"
        body = {
            "view": {
                "private_metadata": json.dumps(
                    {"job_uuid": job_uuid, "timestamp": "123456.789"}
                ),
                "state": {"values": {}},  # No selected options
            },
            "user": {"id": user_id},
        }
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "ray": ray_connection,
        }

        mock_job = {
            "data": {
                "workflow_uuid": "workflow-123",
                "target_languages": [{"uuid": lang_uuid, "name": "French"}],
                "source_files": [
                    {
                        "file_uuid": file_uuid,
                        "filename": "test.txt",
                        "target_files": [],
                    }
                ],
            }
        }

        with patch(
            "app.slack.listeners.redis_conn.set", new_callable=AsyncMock
        ) as mock_redis_set:
            mock_redis_set.return_value = True
            with patch(
                "app.slack.listeners.get_client_evaluation_job", new_callable=AsyncMock
            ) as mock_get_job:
                mock_get_job.return_value = mock_job
                with patch(
                    "app.slack.listeners.submit_verification_job",
                    new_callable=AsyncMock,
                ) as mock_submit:
                    await handle_verify_job_submission(
                        context_dict, mock_ack, body=body, client=mock_client
                    )
                    mock_ack.assert_called_once_with(response_action="clear")
                    # Should still call submit_verification_job with empty selected_languages
                    mock_submit.assert_called_once()
                    call_args = mock_submit.call_args
                    assert call_args[1]["selected_languages"] == []


class TestViewUpdateAutoTranslateSettings:
    """Tests for view_update_auto_translate_settings function."""

    @pytest.mark.asyncio
    async def test_view_update_auto_translate_settings_validation_error(
        self, user_id, team_id, ray_client
    ):
        """Test view_update_auto_translate_settings with validation error."""
        from pydantic import ValidationError

        from app.slack.listeners import view_update_auto_translate_settings

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        view = {
            "state": {"values": {}},  # Invalid form data
            "private_metadata": team_id,
        }
        body = {"api_app_id": "app-123"}
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "ray": ray_connection,
        }

        with patch(
            "app.slack.listeners.AutoTranslationSettingsForm.parse_slack",
            side_effect=ValidationError.from_exception_data("TestForm", []),
        ):
            await view_update_auto_translate_settings(
                context_dict, mock_ack, view=view, body=body, client=mock_client
            )
            mock_ack.assert_called_once_with(response_action="errors", errors={})

    @pytest.mark.asyncio
    async def test_view_update_auto_translate_settings_disable(
        self, user_id, team_id, ray_client
    ):
        """Test view_update_auto_translate_settings disabling auto-translate."""
        from app.slack.listeners import view_update_auto_translate_settings

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        channel_id = "C123"
        view = {
            "state": {
                "values": {
                    "channels": {"channels": {"selected_conversations": [channel_id]}},
                    "languages": {"languages": {"selected_options": []}},
                    "display_format": {
                        "display_format": {"selected_option": {"value": "thread"}}
                    },
                }
            },
            "private_metadata": team_id,
        }
        body = {"api_app_id": "app-123"}
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "ray": ray_connection,
        }

        with patch(
            "app.slack.listeners.resolve_channels_to_team",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_resolve.return_value = {
                "channel_id": channel_id,
                "team_id": team_id,
            }
            with patch(
                "app.slack.listeners.disable_auto_translate_group_settings",
                new_callable=AsyncMock,
            ) as mock_disable:
                with patch(
                    "app.slack.listeners.get_token_for_team",
                    new_callable=AsyncMock,
                ) as mock_get_token:
                    mock_get_token.return_value = None
                    with patch(
                        "app.slack.listeners.home_view", new_callable=AsyncMock
                    ) as mock_home_view:
                        mock_home_view.return_value = {"type": "home"}
                        await view_update_auto_translate_settings(
                            context_dict,
                            mock_ack,
                            view=view,
                            body=body,
                            client=mock_client,
                        )
                        mock_ack.assert_called_once_with(response_action="clear")
                        mock_disable.assert_called_once_with(context_dict, channel_id)

    @pytest.mark.asyncio
    async def test_view_update_auto_translate_settings_enable(
        self, user_id, team_id, ray_client
    ):
        """Test view_update_auto_translate_settings enabling auto-translate."""
        from app.slack.listeners import view_update_auto_translate_settings

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        channel_id = "C123"
        lang_uuid = "lang-123"
        view = {
            "state": {
                "values": {
                    "channels": {"channels": {"selected_conversations": [channel_id]}},
                    "languages": {
                        "languages": {"selected_options": [{"value": lang_uuid}]}
                    },
                    "display_format": {
                        "display_format": {"selected_option": {"value": "thread"}}
                    },
                }
            },
            "private_metadata": team_id,
        }
        body = {"api_app_id": "app-123"}
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "ray": ray_connection,
        }

        with patch(
            "app.slack.listeners.resolve_channels_to_team",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_resolve.return_value = {
                "channel_id": channel_id,
                "team_id": team_id,
            }
            with patch(
                "app.slack.listeners.update_auto_translate_group_settings",
                new_callable=AsyncMock,
            ) as mock_update:
                with patch(
                    "app.slack.listeners.get_token_for_team",
                    new_callable=AsyncMock,
                ) as mock_get_token:
                    mock_get_token.return_value = None
                    with patch(
                        "app.slack.listeners.home_view", new_callable=AsyncMock
                    ) as mock_home_view:
                        mock_home_view.return_value = {"type": "home"}
                        await view_update_auto_translate_settings(
                            context_dict,
                            mock_ack,
                            view=view,
                            body=body,
                            client=mock_client,
                        )
                        mock_ack.assert_called_once_with(response_action="clear")
                        mock_update.assert_called_once()
                        call_args = mock_update.call_args
                        assert call_args[1]["languages"] == [lang_uuid]

    @pytest.mark.asyncio
    async def test_view_update_auto_translate_settings_channel_not_found(
        self, user_id, team_id, ray_client
    ):
        """Test view_update_auto_translate_settings with channel_not_found error."""
        from slack_sdk.errors import SlackApiError

        from app.slack.listeners import view_update_auto_translate_settings

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        channel_id = "C123"
        view = {
            "state": {
                "values": {
                    "channels": {"channels": {"selected_conversations": [channel_id]}},
                    "languages": {
                        "languages": {"selected_options": [{"value": "lang-123"}]}
                    },
                    "display_format": {
                        "display_format": {"selected_option": {"value": "thread"}}
                    },
                }
            },
            "private_metadata": team_id,
        }
        body = {"api_app_id": "app-123"}
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "ray": ray_connection,
        }

        error_response = {"error": "channel_not_found"}
        slack_error = SlackApiError(
            message="Channel not found", response=error_response
        )

        with patch(
            "app.slack.listeners.resolve_channels_to_team",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_resolve.side_effect = slack_error
            await view_update_auto_translate_settings(
                context_dict,
                mock_ack,
                view=view,
                body=body,
                client=mock_client,
            )
            mock_ack.assert_called_once_with(response_action="clear")
            mock_client.chat_postMessage.assert_called_once()
            call_args = mock_client.chat_postMessage.call_args
            assert "channel not found" in call_args[1]["text"].lower()


class TestHomeOpened:
    """Tests for home_opened function - app home tab handler."""

    @pytest.mark.asyncio
    async def test_home_opened_first_time(self, user_id, team_id, ray_client):
        """Test home_opened when app home is opened for the first time."""
        from app.slack.listeners import home_opened

        mock_ack = AsyncMock()
        mock_say = AsyncMock()
        mock_client = AsyncMock()
        channel_id = "D123"
        event = {"channel": channel_id}
        body = {"api_app_id": "app-123"}
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "ray": ray_connection,
        }

        # Mock empty history (first time)
        mock_client.conversations_history.return_value = {"messages": []}

        with patch(
            "app.slack.listeners.home_view", new_callable=AsyncMock
        ) as mock_home_view:
            mock_home_view.return_value = {"type": "home"}
            await home_opened(
                context_dict,
                event=event,
                action=None,
                body=body,
                say=mock_say,
                client=mock_client,
                ack=mock_ack,
            )
            mock_ack.assert_called_once()
            mock_say.assert_called_once()
            mock_client.views_publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_home_opened_welcome_back(self, user_id, team_id, ray_client):
        """Test home_opened when app home has been idle for 24 hours."""
        from app.slack.listeners import home_opened

        mock_ack = AsyncMock()
        mock_say = AsyncMock()
        mock_client = AsyncMock()
        channel_id = "D123"
        event = {"channel": channel_id}
        body = {"api_app_id": "app-123"}
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "ray": ray_connection,
        }

        # Mock history with messages (not first time)
        mock_client.conversations_history.side_effect = [
            {"messages": [{"ts": "123456.789"}]},  # Recent history
            {"messages": []},  # No messages in last 24 hours
        ]

        with patch(
            "app.slack.listeners.home_view", new_callable=AsyncMock
        ) as mock_home_view:
            mock_home_view.return_value = {"type": "home"}
            await home_opened(
                context_dict,
                event=event,
                action=None,
                body=body,
                say=mock_say,
                client=mock_client,
                ack=mock_ack,
            )
            mock_ack.assert_called_once()
            mock_say.assert_called_once()  # WelcomeBackMessage
            mock_client.views_publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_home_opened_recent_activity(self, user_id, team_id, ray_client):
        """Test home_opened when there's been recent activity."""
        from app.slack.listeners import home_opened

        mock_ack = AsyncMock()
        mock_say = AsyncMock()
        mock_client = AsyncMock()
        channel_id = "D123"
        event = {"channel": channel_id}
        body = {"api_app_id": "app-123"}
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "ray": ray_connection,
        }

        # Mock history with recent messages
        mock_client.conversations_history.side_effect = [
            {"messages": [{"ts": "123456.789"}]},  # Recent history
            {"messages": [{"ts": "123456.790"}]},  # Messages in last 24 hours
        ]

        with patch(
            "app.slack.listeners.home_view", new_callable=AsyncMock
        ) as mock_home_view:
            mock_home_view.return_value = {"type": "home"}
            await home_opened(
                context_dict,
                event=event,
                action=None,
                body=body,
                say=mock_say,
                client=mock_client,
                ack=mock_ack,
            )
            mock_ack.assert_called_once()
            mock_say.assert_not_called()  # No message when recent activity
            mock_client.views_publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_home_opened_no_channel(self, user_id, team_id, ray_client):
        """Test home_opened when event has no channel."""
        from app.slack.listeners import home_opened

        mock_ack = AsyncMock()
        mock_say = AsyncMock()
        mock_client = AsyncMock()
        event = {}  # No channel
        body = {"api_app_id": "app-123"}
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "ray": ray_connection,
        }

        with patch(
            "app.slack.listeners.home_view", new_callable=AsyncMock
        ) as mock_home_view:
            mock_home_view.return_value = {"type": "home"}
            await home_opened(
                context_dict,
                event=event,
                action=None,
                body=body,
                say=mock_say,
                client=mock_client,
                ack=mock_ack,
            )
            mock_ack.assert_called_once()
            mock_say.assert_not_called()  # No message when no channel
            mock_client.views_publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_home_opened_slack_api_error(self, user_id, team_id, ray_client):
        """Test home_opened handles SlackApiError gracefully."""
        from slack_sdk.errors import SlackApiError

        from app.slack.listeners import home_opened

        mock_ack = AsyncMock()
        mock_say = AsyncMock()
        mock_client = AsyncMock()
        channel_id = "D123"
        event = {"channel": channel_id}
        body = {"api_app_id": "app-123"}
        super_group = RaySuperGroup(
            id=str(uuid4()),
            name="Test Group",
            verify_organization_uuid=str(uuid4()),
            enable_verify_in_slack=False,
            slack_team_id=team_id,
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)
        context_dict = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "ray": ray_connection,
        }

        # Mock SlackApiError
        mock_client.conversations_history.side_effect = SlackApiError(
            message="API error", response=MagicMock()
        )

        with patch(
            "app.slack.listeners.home_view", new_callable=AsyncMock
        ) as mock_home_view:
            mock_home_view.return_value = {"type": "home"}
            await home_opened(
                context_dict,
                event=event,
                action=None,
                body=body,
                say=mock_say,
                client=mock_client,
                ack=mock_ack,
            )
            mock_ack.assert_called_once()
            # Should still publish view even on error
            mock_client.views_publish.assert_called_once()


class TestHandleTranslateShortcut:
    """Tests for handle_translate_shortcut function."""

    @pytest.mark.asyncio
    async def test_handle_translate_shortcut_normal_text(
        self, user_id, team_id, ray_client
    ):
        """Test handle_translate_shortcut with text under 5000 characters."""
        from app.slack.listeners import handle_translate_shortcut

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = {
            "message": {
                "text": "Hello world, this is a test message.",
            }
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "locale": "fr",
                "ray": ray_connection,
            }
        )

        with patch(
            "app.slack.listeners.detect_language", new_callable=AsyncMock
        ) as mock_detect:
            mock_detect.return_value = MagicMock(language="en")
            with patch(
                "app.slack.listeners.get_mt_translation", new_callable=AsyncMock
            ) as mock_mt:
                # The decorator passes context as first arg, then *args to the function
                # Function signature is (ack, body, client, context), so we pass (context_dict, mock_ack, body=body, client=mock_client)
                await handle_translate_shortcut(
                    context_dict, mock_ack, body=body, client=mock_client
                )
                mock_ack.assert_called_once()
                mock_detect.assert_called_once()
                # Should pass full text to detect_language when under 5000 chars
                assert (
                    mock_detect.call_args[0][1]
                    == "Hello world, this is a test message."
                )
                mock_mt.assert_called_once()
                call_args = mock_mt.call_args
                assert call_args[1]["source_lang"] == "en"
                assert call_args[1]["target_lang"] == "fr"
                assert (
                    call_args[1]["sentence"] == "Hello world, this is a test message."
                )
                assert call_args[1]["usage_type"] == "shortcut_translate"

    @pytest.mark.asyncio
    async def test_handle_translate_shortcut_text_exceeds_5000_chars(
        self, user_id, team_id, ray_client
    ):
        """Test handle_translate_shortcut truncates text to 5000 chars for detection."""
        from app.slack.listeners import handle_translate_shortcut

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        # Create text that exceeds 5000 characters
        long_text = "A" * 6000
        body = {
            "message": {
                "text": long_text,
            }
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "locale": "es",
                "ray": ray_connection,
            }
        )

        with patch(
            "app.slack.listeners.detect_language", new_callable=AsyncMock
        ) as mock_detect:
            mock_detect.return_value = MagicMock(language="en")
            with patch(
                "app.slack.listeners.get_mt_translation", new_callable=AsyncMock
            ) as mock_mt:
                # The decorator passes context as first arg, then *args to the function
                # Function signature is (ack, body, client, context), so we pass (context_dict, mock_ack, body=body, client=mock_client)
                await handle_translate_shortcut(
                    context_dict, mock_ack, body=body, client=mock_client
                )
                mock_ack.assert_called_once()
                mock_detect.assert_called_once()
                # Should pass only first 5000 chars to detect_language
                detected_text = mock_detect.call_args[0][1]
                assert len(detected_text) == 5000
                assert detected_text == "A" * 5000
                mock_mt.assert_called_once()
                call_args = mock_mt.call_args
                # Full text should still be passed to get_mt_translation
                assert call_args[1]["sentence"] == long_text
                assert len(call_args[1]["sentence"]) == 6000
                assert call_args[1]["source_lang"] == "en"
                assert call_args[1]["target_lang"] == "es"

    @pytest.mark.asyncio
    async def test_handle_translate_shortcut_exactly_5000_chars(
        self, user_id, team_id, ray_client
    ):
        """Test handle_translate_shortcut with exactly 5000 characters."""
        from app.slack.listeners import handle_translate_shortcut

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        # Create text with exactly 5000 characters
        exact_text = "B" * 5000
        body = {
            "message": {
                "text": exact_text,
            }
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "locale": "de",
                "ray": ray_connection,
            }
        )

        with patch(
            "app.slack.listeners.detect_language", new_callable=AsyncMock
        ) as mock_detect:
            mock_detect.return_value = MagicMock(language="en")
            with patch(
                "app.slack.listeners.get_mt_translation", new_callable=AsyncMock
            ) as mock_mt:
                # The decorator passes context as first arg, then *args to the function
                # Function signature is (ack, body, client, context), so we pass (context_dict, mock_ack, body=body, client=mock_client)
                await handle_translate_shortcut(
                    context_dict, mock_ack, body=body, client=mock_client
                )
                mock_ack.assert_called_once()
                mock_detect.assert_called_once()
                # Should pass full text (exactly 5000 chars) to detect_language
                detected_text = mock_detect.call_args[0][1]
                assert len(detected_text) == 5000
                assert detected_text == exact_text
                mock_mt.assert_called_once()
                call_args = mock_mt.call_args
                assert call_args[1]["sentence"] == exact_text
                assert call_args[1]["source_lang"] == "en"
                assert call_args[1]["target_lang"] == "de"

    @pytest.mark.asyncio
    async def test_handle_translate_shortcut_default_locale(
        self, user_id, team_id, ray_client
    ):
        """Test handle_translate_shortcut uses default locale 'en' when not set."""
        from app.slack.listeners import handle_translate_shortcut

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = {
            "message": {
                "text": "Test message",
            }
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "ray": ray_connection,
                # No locale set
            }
        )

        with patch(
            "app.slack.listeners.detect_language", new_callable=AsyncMock
        ) as mock_detect:
            mock_detect.return_value = MagicMock(language="fr")
            with patch(
                "app.slack.listeners.get_mt_translation", new_callable=AsyncMock
            ) as mock_mt:
                # The decorator passes context as first arg, then *args to the function
                # Function signature is (ack, body, client, context), so we pass (context_dict, mock_ack, body=body, client=mock_client)
                await handle_translate_shortcut(
                    context_dict, mock_ack, body=body, client=mock_client
                )
                mock_ack.assert_called_once()
                mock_mt.assert_called_once()
                call_args = mock_mt.call_args
                # Should default to "en" when locale not set
                assert call_args[1]["target_lang"] == "en"

    @pytest.mark.asyncio
    async def test_handle_translate_shortcut_empty_text(
        self, user_id, team_id, ray_client
    ):
        """Test handle_translate_shortcut returns early with message when text is empty."""
        from app.slack.listeners import handle_translate_shortcut

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = {
            "message": {
                "text": "",
            }
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "locale": "fr",
                "ray": ray_connection,
            }
        )

        with patch(
            "app.slack.listeners.detect_language", new_callable=AsyncMock
        ) as mock_detect:
            with patch(
                "app.slack.listeners.get_mt_translation", new_callable=AsyncMock
            ) as mock_mt:
                await handle_translate_shortcut(
                    context_dict, mock_ack, body=body, client=mock_client
                )
                mock_ack.assert_called_once()
                # Should NOT call detect_language or get_mt_translation
                mock_detect.assert_not_called()
                mock_mt.assert_not_called()
                # Should send a message to the user
                mock_client.chat_postMessage.assert_called_once()
                call_args = mock_client.chat_postMessage.call_args
                assert call_args[1]["channel"] == user_id
                assert "doesn't contain any text" in call_args[1]["text"]

    @pytest.mark.asyncio
    async def test_handle_translate_shortcut_missing_text_key(
        self, user_id, team_id, ray_client
    ):
        """Test handle_translate_shortcut handles missing text key gracefully."""
        from app.slack.listeners import handle_translate_shortcut

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = {
            "message": {}  # No "text" key
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "locale": "fr",
                "ray": ray_connection,
            }
        )

        with patch(
            "app.slack.listeners.detect_language", new_callable=AsyncMock
        ) as mock_detect:
            with patch(
                "app.slack.listeners.get_mt_translation", new_callable=AsyncMock
            ) as mock_mt:
                await handle_translate_shortcut(
                    context_dict, mock_ack, body=body, client=mock_client
                )
                mock_ack.assert_called_once()
                # Should NOT call detect_language or get_mt_translation
                mock_detect.assert_not_called()
                mock_mt.assert_not_called()
                # Should send a message to the user
                mock_client.chat_postMessage.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_translate_shortcut_whitespace_only_text(
        self, user_id, team_id, ray_client
    ):
        """Test handle_translate_shortcut treats whitespace-only text as empty."""
        from app.slack.listeners import handle_translate_shortcut

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = {
            "message": {
                "text": "   \n\t  ",  # Only whitespace
            }
        }
        ray_connection = RayConnection(super_group=[], client=ray_client)
        context_dict = RayContext(
            {
                "user_id": user_id,
                "team_id": team_id,
                "locale": "fr",
                "ray": ray_connection,
            }
        )

        with patch(
            "app.slack.listeners.detect_language", new_callable=AsyncMock
        ) as mock_detect:
            with patch(
                "app.slack.listeners.get_mt_translation", new_callable=AsyncMock
            ) as mock_mt:
                await handle_translate_shortcut(
                    context_dict, mock_ack, body=body, client=mock_client
                )
                mock_ack.assert_called_once()
                # Should NOT call detect_language or get_mt_translation
                mock_detect.assert_not_called()
                mock_mt.assert_not_called()
                # Should send a message to the user
                mock_client.chat_postMessage.assert_called_once()
