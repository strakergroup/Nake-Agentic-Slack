"""Tests for app.slack.buglog_notifier module."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from app.slack.buglog_notifier import (
    _schedule_dev_alert_notification,
    _send_to_google_chat,
    notify_exception,
    notify_message,
)


async def _fake_send_to_google_chat(*_a: object, **_k: object) -> None:
    """Awaitable stand-in for _send_to_google_chat in schedule tests."""
    return None


def _make_mock_async_client() -> tuple[MagicMock, MagicMock]:
    """Return (AsyncClient mock factory result, inner client with .post)."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = ""
    mock_inner = MagicMock()
    mock_inner.post = AsyncMock(return_value=mock_response)
    mock_inner.__aenter__ = AsyncMock(return_value=mock_inner)
    mock_inner.__aexit__ = AsyncMock(return_value=None)
    mock_ac = MagicMock(return_value=mock_inner)
    return mock_ac, mock_inner


class TestSendToGoogleChat:
    """Tests for _send_to_google_chat function."""

    @pytest_asyncio.fixture
    async def mock_config(self):
        """Mock config with GOOGLE_CHAT_WEBHOOK_PM (defaults to UAT for env display)."""
        with patch("app.config.config") as mock_config:
            mock_config.google_chat_webhook_pm.get_secret_value.return_value = (
                "https://chat.googleapis.com/v1/spaces/alert/messages?key=test"
            )
            from straker_utils.environment import Environment

            mock_config.environment = Environment.uat
            yield mock_config

    @pytest_asyncio.fixture
    async def mock_http_client(self):
        mock_ac, mock_inner = _make_mock_async_client()
        with patch("app.slack.buglog_notifier.httpx.AsyncClient", mock_ac):
            yield mock_inner

    @pytest.mark.asyncio
    async def test_send_with_exception(self, mock_config, mock_http_client):
        """Test sending exception to Google Chat."""
        exc = ValueError("Test error")
        await _send_to_google_chat(exc=exc, msg="Test message", severity="ERROR")

        mock_http_client.post.assert_called_once()
        call_args = mock_http_client.post.call_args
        assert call_args[0][0] == (
            "https://chat.googleapis.com/v1/spaces/alert/messages?key=test"
        )
        payload = call_args.kwargs["json"]
        assert "text" in payload
        assert "Test message" in payload["text"] or "ValueError" in payload["text"]
        assert "Traceback" in payload["text"]

    @pytest.mark.asyncio
    async def test_send_with_message_only(self, mock_config, mock_http_client):
        """Test sending message-only alert."""
        await _send_to_google_chat(msg="Test alert message", severity="INFO")

        mock_http_client.post.assert_called_once()
        call_args = mock_http_client.post.call_args
        assert call_args[0][0] == (
            "https://chat.googleapis.com/v1/spaces/alert/messages?key=test"
        )
        assert "Test alert message" in call_args.kwargs["json"]["text"]

    @pytest.mark.asyncio
    async def test_send_with_extra_info(self, mock_config, mock_http_client):
        """Test sending with extra information."""
        extra = {"key1": "value1", "key2": "value2"}
        await _send_to_google_chat(exc=ValueError("Error"), extra=extra)

        text = mock_http_client.post.call_args.kwargs["json"]["text"]
        assert "Extra:" in text
        assert "key1" in text and "value1" in text

    @pytest.mark.asyncio
    async def test_send_missing_webhook_url(self, mock_config, mock_http_client):
        """Test that empty GOOGLE_CHAT_WEBHOOK_PM skips the request."""
        mock_config.google_chat_webhook_pm.get_secret_value.return_value = ""
        await _send_to_google_chat(exc=ValueError("Error"))

        mock_http_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_http_error(self, mock_config, mock_http_client):
        """Test error handling when webhook returns error status."""
        err_resp = MagicMock()
        err_resp.status_code = 500
        err_resp.text = "err"
        mock_http_client.post = AsyncMock(return_value=err_resp)

        await _send_to_google_chat(exc=ValueError("Error"))

        mock_http_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_request_exception(self, mock_config, mock_http_client):
        """Test error handling when httpx raises."""
        mock_http_client.post = AsyncMock(
            side_effect=httpx.ConnectError("connection failed", request=MagicMock())
        )

        await _send_to_google_chat(exc=ValueError("Error"))

        mock_http_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_traceback_present(self, mock_config, mock_http_client):
        """Test that traceback is included for exceptions."""
        try:
            raise ValueError("Test error")
        except ValueError as exc:
            await _send_to_google_chat(exc=exc)

        text = mock_http_client.post.call_args.kwargs["json"]["text"]
        assert "Traceback" in text

    @pytest.mark.asyncio
    async def test_send_includes_environment_in_body(
        self, mock_config, mock_http_client
    ):
        """Alert body still reflects deployment environment (e.g. Uat)."""
        await _send_to_google_chat(msg="hello", severity="ERROR")
        text = mock_http_client.post.call_args.kwargs["json"]["text"]
        assert "Environment:" in text


class TestScheduleDevAlertNotification:
    """Tests for _schedule_dev_alert_notification function."""

    @patch("app.config.config")
    @patch("app.slack.buglog_notifier._send_to_google_chat")
    def test_schedule_with_running_loop(self, mock_send, mock_config):
        """Test scheduling in async context with running loop."""
        from straker_utils.environment import Environment

        mock_config.environment = Environment.uat
        mock_send.side_effect = _fake_send_to_google_chat

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:

            async def run_test():
                _schedule_dev_alert_notification(exc=ValueError("Error"))
                await asyncio.sleep(0.01)

            loop.run_until_complete(run_test())
            assert mock_send.called or len(loop._ready) > 0
        finally:
            loop.close()

    @patch("app.config.config")
    @patch("app.slack.buglog_notifier._send_to_google_chat")
    @patch("app.slack.buglog_notifier.asyncio.run")
    def test_schedule_no_loop(self, mock_asyncio_run, mock_send, mock_config):
        """Test scheduling in sync context creates thread."""
        from straker_utils.environment import Environment

        mock_config.environment = Environment.uat

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass

        _schedule_dev_alert_notification(exc=ValueError("Error"))

        import time

        time.sleep(0.1)

    @patch("app.config.config")
    @patch("app.slack.buglog_notifier.logger")
    def test_schedule_error_handling(self, mock_logger, mock_config):
        """Test error handling when scheduling fails unexpectedly."""
        from straker_utils.environment import Environment

        mock_config.environment = Environment.uat

        with patch(
            "app.slack.buglog_notifier.asyncio.get_running_loop",
            side_effect=Exception("Unexpected error"),
        ):
            _schedule_dev_alert_notification(exc=ValueError("Error"))
            mock_logger.error.assert_called()


class TestNotifyException:
    """Tests for notify_exception function."""

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.slack.buglog_notifier._schedule_dev_alert_notification")
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
    @patch("app.slack.buglog_notifier._schedule_dev_alert_notification")
    def test_notify_exception_schedules_chat(self, mock_schedule, mock_buglog_notify):
        """Test that notify_exception schedules Google Chat notification."""
        mock_buglog_notify.return_value = True
        exc = ValueError("Test error")

        notify_exception(exc, "Test message", {"key": "value"}, "ERROR")

        mock_schedule.assert_called_once_with(
            exc, "Test message", {"key": "value"}, "ERROR"
        )

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.slack.buglog_notifier._schedule_dev_alert_notification")
    def test_notify_exception_with_message_only(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test notify_exception with message but no exception."""
        mock_buglog_notify.return_value = True

        notify_exception(msg="Test message")

        mock_schedule.assert_called_once_with(None, "Test message", None, "ERROR")

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.slack.buglog_notifier._schedule_dev_alert_notification")
    def test_notify_exception_no_exception_no_message(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test notify_exception with neither exception nor message."""
        mock_buglog_notify.return_value = True

        notify_exception()

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_not_called()


class TestNotifyMessage:
    """Tests for notify_message function."""

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_dev_alert_notification")
    def test_notify_message_calls_buglog(self, mock_schedule, mock_buglog_notify):
        """Test that notify_message calls buglog."""
        mock_buglog_notify.return_value = True

        result = notify_message("Test message", {"key": "value"}, "INFO")

        mock_buglog_notify.assert_called_once_with(
            "Test message", {"key": "value"}, "INFO"
        )
        assert result is True

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_dev_alert_notification")
    def test_notify_message_schedules_chat_for_error(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test that notify_message schedules Google Chat for ERROR severity."""
        mock_buglog_notify.return_value = True

        notify_message("Test message", {"key": "value"}, "ERROR")

        mock_schedule.assert_called_once_with(
            None, "Test message", {"key": "value"}, "ERROR"
        )

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_dev_alert_notification")
    def test_notify_message_empty_message(self, mock_schedule, mock_buglog_notify):
        """Test notify_message with empty message does not schedule chat."""
        mock_buglog_notify.return_value = True

        notify_message("")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_not_called()

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_dev_alert_notification")
    def test_notify_message_default_severity(self, mock_schedule, mock_buglog_notify):
        """Test notify_message uses INFO as default severity and skips chat."""
        mock_buglog_notify.return_value = True

        notify_message("Test message")

        mock_buglog_notify.assert_called_once_with("Test message", None, "INFO")
        mock_schedule.assert_not_called()

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_dev_alert_notification")
    def test_notify_message_info_severity_skips_chat(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test that INFO severity messages do not trigger dev alert."""
        mock_buglog_notify.return_value = True

        notify_message("Test message", severity="INFO")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_not_called()

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_dev_alert_notification")
    def test_notify_message_error_severity_sends(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test that ERROR severity messages schedule dev alert."""
        mock_buglog_notify.return_value = True

        notify_message("Test error message", severity="ERROR")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_called_once_with(None, "Test error message", None, "ERROR")

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_dev_alert_notification")
    def test_notify_message_warning_severity_sends(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test that WARNING severity messages schedule dev alert."""
        mock_buglog_notify.return_value = True

        notify_message("Test warning message", severity="WARNING")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_called_once_with(
            None, "Test warning message", None, "WARNING"
        )

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.slack.buglog_notifier._schedule_dev_alert_notification")
    def test_notify_message_fatal_severity_sends(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test that FATAL severity messages schedule dev alert."""
        mock_buglog_notify.return_value = True

        notify_message("Test fatal message", severity="FATAL")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_called_once_with(None, "Test fatal message", None, "FATAL")

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.slack.buglog_notifier._schedule_dev_alert_notification")
    def test_notify_exception_info_severity_skips_chat(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test that INFO severity exceptions do not schedule dev alert."""
        mock_buglog_notify.return_value = True
        exc = ValueError("Test error")

        notify_exception(exc, severity="INFO")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_not_called()

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.slack.buglog_notifier._schedule_dev_alert_notification")
    def test_notify_exception_warning_severity_sends(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test that WARNING severity exceptions schedule dev alert."""
        mock_buglog_notify.return_value = True
        exc = ValueError("Test error")

        notify_exception(exc, severity="WARNING")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_called_once_with(exc, None, None, "WARNING")

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.slack.buglog_notifier._schedule_dev_alert_notification")
    def test_notify_exception_fatal_severity_sends(
        self, mock_schedule, mock_buglog_notify
    ):
        """Test that FATAL severity exceptions schedule dev alert."""
        mock_buglog_notify.return_value = True
        exc = ValueError("Test error")

        notify_exception(exc, severity="FATAL")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_called_once_with(exc, None, None, "FATAL")
