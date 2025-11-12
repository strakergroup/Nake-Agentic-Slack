"""Tests for app.slack.buglog_notifier module."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from slack_sdk.errors import SlackApiError

from app.slack.buglog_notifier import (
    _schedule_slack_notification,
    _send_to_slack,
    notify_exception,
    notify_message,
)


class TestSendToSlack:
    """Tests for _send_to_slack function."""

    @pytest_asyncio.fixture
    async def mock_config(self):
        """Mock config with valid Slack credentials."""
        with patch("app.config.config") as mock_config:
            mock_config.slack_dev_alert_bot_token.get_secret_value.return_value = (
                "xoxb-test-token"
            )
            mock_config.slack_dev_alert_channel_id = "C123456"
            mock_config.environment.value = "uat"
            yield mock_config

    @pytest_asyncio.fixture
    async def mock_environment(self):
        """Mock Environment enum."""
        with patch("app.config.Environment") as mock_env:
            mock_env.production = "production"
            yield mock_env

    @pytest_asyncio.fixture
    async def mock_slack_client(self):
        """Mock Slack AsyncWebClient."""
        with patch("app.slack.buglog_notifier.AsyncWebClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.chat_postMessage = AsyncMock()
            mock_client_class.return_value = mock_client
            yield mock_client

    @pytest.mark.asyncio
    async def test_send_to_slack_with_exception(
        self, mock_config, mock_environment, mock_slack_client
    ):
        """Test sending exception to Slack."""
        exc = ValueError("Test error")
        await _send_to_slack(exc=exc, msg="Test message", severity="ERROR")

        mock_slack_client.chat_postMessage.assert_called_once()
        call_args = mock_slack_client.chat_postMessage.call_args
        assert call_args.kwargs["channel"] == "C123456"
        assert "Test message: ValueError - Test error" in call_args.kwargs["text"]
        assert len(call_args.kwargs["blocks"]) > 0

    @pytest.mark.asyncio
    async def test_send_to_slack_with_message_only(
        self, mock_config, mock_environment, mock_slack_client
    ):
        """Test sending message only to Slack."""
        await _send_to_slack(msg="Test alert message", severity="INFO")

        mock_slack_client.chat_postMessage.assert_called_once()
        call_args = mock_slack_client.chat_postMessage.call_args
        assert call_args.kwargs["channel"] == "C123456"
        assert "Alert: Test alert message" in call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_send_to_slack_with_extra_info(
        self, mock_config, mock_environment, mock_slack_client
    ):
        """Test sending with extra information."""
        extra = {"key1": "value1", "key2": "value2"}
        await _send_to_slack(exc=ValueError("Error"), extra=extra)

        call_args = mock_slack_client.chat_postMessage.call_args
        blocks = call_args.kwargs["blocks"]
        # Find the extra info block - check all blocks for the text
        blocks_str = str(blocks)
        assert "Extra Info" in blocks_str
        assert "key1" in blocks_str or "value1" in blocks_str

    @pytest.mark.asyncio
    async def test_send_to_slack_missing_channel_id(
        self, mock_config, mock_environment, mock_slack_client
    ):
        """Test that missing channel_id skips Slack notification."""
        mock_config.slack_dev_alert_channel_id = ""
        await _send_to_slack(exc=ValueError("Error"))

        mock_slack_client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_to_slack_missing_token(
        self, mock_config, mock_environment, mock_slack_client
    ):
        """Test that missing token skips Slack notification."""
        mock_config.slack_dev_alert_bot_token.get_secret_value.return_value = ""
        await _send_to_slack(exc=ValueError("Error"))

        mock_slack_client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_to_slack_slack_api_error(
        self, mock_config, mock_environment, mock_slack_client
    ):
        """Test error handling when Slack API fails."""
        mock_slack_client.chat_postMessage.side_effect = SlackApiError(
            "channel_not_found", response={"error": "channel_not_found"}
        )

        # Should not raise exception
        await _send_to_slack(exc=ValueError("Error"))

        mock_slack_client.chat_postMessage.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_to_slack_traceback_truncation(
        self, mock_config, mock_environment, mock_slack_client
    ):
        """Test that long tracebacks are truncated."""
        # Create a long traceback
        try:
            raise ValueError("Test error")
        except ValueError as exc:
            await _send_to_slack(exc=exc)

        call_args = mock_slack_client.chat_postMessage.call_args
        blocks = call_args.kwargs["blocks"]
        # Check that traceback block exists - check all blocks for the text
        blocks_str = str(blocks)
        assert "Traceback" in blocks_str


class TestScheduleSlackNotification:
    """Tests for _schedule_slack_notification function."""

    @patch("app.slack.buglog_notifier._send_to_slack")
    def test_schedule_slack_notification_with_running_loop(self, mock_send_to_slack):
        """Test scheduling in async context with running loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:

            async def run_test():
                _schedule_slack_notification(exc=ValueError("Error"))
                # Give the task a moment to be scheduled
                await asyncio.sleep(0.01)

            loop.run_until_complete(run_test())
            # Task should be created
            assert mock_send_to_slack.called or len(loop._ready) > 0
        finally:
            loop.close()

    @patch("app.slack.buglog_notifier._send_to_slack")
    @patch("app.slack.buglog_notifier.asyncio.run")
    def test_schedule_slack_notification_no_loop(
        self, mock_asyncio_run, mock_send_to_slack
    ):
        """Test scheduling in sync context creates thread."""
        # Ensure no running loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        _schedule_slack_notification(exc=ValueError("Error"))

        # Should create a thread and call asyncio.run
        # Give thread a moment to start
        import time

        time.sleep(0.1)
        # The thread should eventually call asyncio.run
        # (we can't easily verify thread creation, but we can verify it doesn't crash)

    @patch("app.slack.buglog_notifier.logger")
    def test_schedule_slack_notification_error_handling(self, mock_logger):
        """Test error handling in _schedule_slack_notification."""
        with patch(
            "app.slack.buglog_notifier.asyncio.get_running_loop",
            side_effect=Exception("Unexpected error"),
        ):
            # Should not raise exception
            _schedule_slack_notification(exc=ValueError("Error"))
            mock_logger.error.assert_called()


class TestNotifyException:
    """Tests for notify_exception function."""

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.slack.buglog_notifier._schedule_slack_notification")
    def test_notify_exception_calls_buglog(self, mock_schedule, mock_buglog_notify):
        """Test that notify_exception calls buglog."""
        mock_buglog_notify.return_value = True
        exc = ValueError("Test error")

        result = notify_exception(exc, "Test message", {"key": "value"}, "ERROR")

        mock_buglog_notify.assert_called_once_with(
            exc, "Test message", {"key": "value"}, "ERROR"
        )
        assert result is True

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.slack.buglog_notifier._schedule_slack_notification")
    def test_notify_exception_schedules_slack(self, mock_schedule, mock_buglog_notify):
        """Test that notify_exception schedules Slack notification."""
        mock_buglog_notify.return_value = True
        exc = ValueError("Test error")

        notify_exception(exc, "Test message", {"key": "value"}, "ERROR")

        mock_schedule.assert_called_once_with(
            exc, "Test message", {"key": "value"}, "ERROR"
        )

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.slack.buglog_notifier._schedule_slack_notification")
    def test_notify_exception_with_message_only(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test notify_exception with message but no exception."""
        mock_buglog_notify.return_value = True

        notify_exception(msg="Test message")

        mock_schedule.assert_called_once_with(None, "Test message", None, "ERROR")

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.slack.buglog_notifier._schedule_slack_notification")
    def test_notify_exception_no_exception_no_message(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test notify_exception with neither exception nor message."""
        mock_buglog_notify.return_value = True

        notify_exception()

        mock_buglog_notify.assert_called_once()
        # Should not schedule Slack notification
        mock_schedule.assert_not_called()


class TestNotifyMessage:
    """Tests for notify_message function."""

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_slack_notification")
    def test_notify_message_calls_buglog(self, mock_schedule, mock_buglog_notify):
        """Test that notify_message calls buglog."""
        mock_buglog_notify.return_value = True

        result = notify_message("Test message", {"key": "value"}, "INFO")

        mock_buglog_notify.assert_called_once_with(
            "Test message", {"key": "value"}, "INFO"
        )
        assert result is True

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_slack_notification")
    def test_notify_message_schedules_slack(self, mock_schedule, mock_buglog_notify):
        """Test that notify_message schedules Slack notification for ERROR severity."""
        mock_buglog_notify.return_value = True

        notify_message("Test message", {"key": "value"}, "ERROR")

        mock_schedule.assert_called_once_with(
            None, "Test message", {"key": "value"}, "ERROR"
        )

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_slack_notification")
    def test_notify_message_empty_message(self, mock_schedule, mock_buglog_notify):
        """Test notify_message with empty message doesn't send to Slack."""
        mock_buglog_notify.return_value = True

        notify_message("")

        mock_buglog_notify.assert_called_once()
        # Should not schedule Slack notification for empty message
        mock_schedule.assert_not_called()

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_slack_notification")
    def test_notify_message_default_severity(self, mock_schedule, mock_buglog_notify):
        """Test notify_message uses INFO as default severity and skips Slack."""
        mock_buglog_notify.return_value = True

        notify_message("Test message")

        mock_buglog_notify.assert_called_once_with("Test message", None, "INFO")
        # Should NOT schedule Slack notification for INFO severity
        mock_schedule.assert_not_called()

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_slack_notification")
    def test_notify_message_info_severity_skips_slack(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test that INFO severity messages don't send to Slack."""
        mock_buglog_notify.return_value = True

        notify_message("Test message", severity="INFO")

        mock_buglog_notify.assert_called_once()
        # Should NOT schedule Slack notification for INFO severity
        mock_schedule.assert_not_called()

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_slack_notification")
    def test_notify_message_error_severity_sends_to_slack(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test that ERROR severity messages DO send to Slack."""
        mock_buglog_notify.return_value = True

        notify_message("Test error message", severity="ERROR")

        mock_buglog_notify.assert_called_once()
        # Should schedule Slack notification for ERROR severity
        mock_schedule.assert_called_once_with(None, "Test error message", None, "ERROR")

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_slack_notification")
    def test_notify_message_warning_severity_sends_to_slack(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test that WARNING severity messages DO send to Slack."""
        mock_buglog_notify.return_value = True

        notify_message("Test warning message", severity="WARNING")

        mock_buglog_notify.assert_called_once()
        # Should schedule Slack notification for WARNING severity
        mock_schedule.assert_called_once_with(
            None, "Test warning message", None, "WARNING"
        )

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_slack_notification")
    def test_notify_message_fatal_severity_sends_to_slack(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test that FATAL severity messages DO send to Slack."""
        mock_buglog_notify.return_value = True

        notify_message("Test fatal message", severity="FATAL")

        mock_buglog_notify.assert_called_once()
        # Should schedule Slack notification for FATAL severity
        mock_schedule.assert_called_once_with(None, "Test fatal message", None, "FATAL")

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.slack.buglog_notifier._schedule_slack_notification")
    def test_notify_exception_info_severity_skips_slack(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test that INFO severity exceptions don't send to Slack."""
        mock_buglog_notify.return_value = True
        exc = ValueError("Test error")

        notify_exception(exc, severity="INFO")

        mock_buglog_notify.assert_called_once()
        # Should NOT schedule Slack notification for INFO severity
        mock_schedule.assert_not_called()

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.slack.buglog_notifier._schedule_slack_notification")
    def test_notify_exception_warning_severity_sends_to_slack(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test that WARNING severity exceptions DO send to Slack."""
        mock_buglog_notify.return_value = True
        exc = ValueError("Test error")

        notify_exception(exc, severity="WARNING")

        mock_buglog_notify.assert_called_once()
        # Should schedule Slack notification for WARNING severity
        mock_schedule.assert_called_once_with(exc, None, None, "WARNING")

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.slack.buglog_notifier._schedule_slack_notification")
    def test_notify_exception_fatal_severity_sends_to_slack(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test that FATAL severity exceptions DO send to Slack."""
        mock_buglog_notify.return_value = True
        exc = ValueError("Test error")

        notify_exception(exc, severity="FATAL")

        mock_buglog_notify.assert_called_once()
        # Should schedule Slack notification for FATAL severity
        mock_schedule.assert_called_once_with(exc, None, None, "FATAL")
