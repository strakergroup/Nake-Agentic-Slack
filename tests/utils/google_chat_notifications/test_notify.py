"""Tests for app.utils.google_chat_notifications.notify."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from straker_utils.environment import Environment

from app.utils.google_chat_notifications.notify import (
    GoogleChatContext,
    build_google_chat_text,
    post_google_chat_notification,
    _send_google_chat_notification,
)


async def _fake_send(*_a: object, **_k: object) -> None:
    return None


def _make_mock_async_client() -> tuple[MagicMock, MagicMock]:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = ""
    mock_inner = MagicMock()
    mock_inner.post = AsyncMock(return_value=mock_response)
    mock_inner.__aenter__ = AsyncMock(return_value=mock_inner)
    mock_inner.__aexit__ = AsyncMock(return_value=None)
    mock_ac = MagicMock(return_value=mock_inner)
    return mock_ac, mock_inner


class TestBuildGoogleChatText:
    def test_exception_heading_same_for_buglog_context(self) -> None:
        text = build_google_chat_text(
            ValueError("x"),
            "ctx",
            None,
            "ERROR",
            "Uat",
            context=GoogleChatContext.BUGLOG,
        )
        assert "\U0001f6a8 Exception" in text
        assert "buglog" not in text.lower()

    def test_app_exception_heading(self) -> None:
        text = build_google_chat_text(
            ValueError("x"),
            "ctx",
            None,
            "ERROR",
            "Uat",
            context=GoogleChatContext.APP,
        )
        assert "\U0001f6a8 Exception" in text
        assert "buglog" not in text.lower()

    def test_app_alert_heading(self) -> None:
        text = build_google_chat_text(
            None,
            "hello",
            None,
            "ERROR",
            "Uat",
            context=GoogleChatContext.APP,
        )
        assert "\U0001f6a8 Alert\n" in text or text.startswith("\U0001f6a8 Alert")
        assert "buglog" not in text.lower()

    def test_info_message_uses_notice_heading(self) -> None:
        text = build_google_chat_text(
            None,
            "FYI",
            None,
            "INFO",
            "Uat",
            context=GoogleChatContext.BUGLOG,
        )
        assert "Notice" in text
        assert "\U00002139 " in text
        assert "buglog" not in text.lower()

    def test_error_message_only_still_alert(self) -> None:
        text = build_google_chat_text(
            None,
            "bad",
            None,
            "ERROR",
            "Uat",
            context=GoogleChatContext.APP,
        )
        assert "Alert" in text
        assert "Notice" not in text.split("\n")[0]


class TestSendGoogleChatNotification:
    @pytest_asyncio.fixture
    async def mock_config(self):
        mock_cfg = MagicMock()
        mock_cfg.google_chat_webhook.get_secret_value.return_value = (
            "https://chat.googleapis.com/v1/spaces/alert/messages?key=test"
        )
        mock_cfg.environment = Environment.uat
        with patch(
            "app.utils.google_chat_notifications.notify._runtime_env_and_config",
            return_value=(Environment, mock_cfg),
        ):
            yield mock_cfg

    @pytest_asyncio.fixture
    async def mock_http_client(self):
        mock_ac, mock_inner = _make_mock_async_client()
        with patch("app.utils.google_chat_notifications.notify.httpx.AsyncClient", mock_ac):
            yield mock_inner

    @pytest.mark.asyncio
    async def test_send_with_exception(self, mock_config, mock_http_client):
        exc = ValueError("Test error")
        await _send_google_chat_notification(
            exc=exc,
            msg="Test message",
            severity="ERROR",
            context=GoogleChatContext.BUGLOG,
        )

        mock_http_client.post.assert_called_once()
        call_args = mock_http_client.post.call_args
        assert call_args[0][0] == (
            "https://chat.googleapis.com/v1/spaces/alert/messages?key=test"
        )
        payload = call_args.kwargs["json"]
        assert "text" in payload
        assert "Test message" in payload["text"] or "ValueError" in payload["text"]
        assert "Exception" in payload["text"]
        assert "buglog" not in payload["text"].lower()
        assert "Error Type: ValueError" in payload["text"]
        assert "Traceback:" in payload["text"]

    @pytest.mark.asyncio
    async def test_send_with_message_only(self, mock_config, mock_http_client):
        await _send_google_chat_notification(
            msg="Test alert message",
            severity="INFO",
            context=GoogleChatContext.BUGLOG,
        )

        mock_http_client.post.assert_called_once()
        call_args = mock_http_client.post.call_args
        text = call_args.kwargs["json"]["text"]
        assert "Test alert message" in text
        assert "Notice" in text
        assert "buglog" not in text.lower()

    @pytest.mark.asyncio
    async def test_send_with_extra_info(self, mock_config, mock_http_client):
        extra = {"key1": "value1", "key2": "value2"}
        await _send_google_chat_notification(
            exc=ValueError("Error"), extra=extra, context=GoogleChatContext.BUGLOG
        )

        text = mock_http_client.post.call_args.kwargs["json"]["text"]
        assert "Extra:" in text
        assert "key1" in text and "value1" in text

    @pytest.mark.asyncio
    async def test_send_missing_webhook_url(self, mock_config, mock_http_client):
        mock_config.google_chat_webhook.get_secret_value.return_value = ""
        await _send_google_chat_notification(exc=ValueError("Error"))

        mock_http_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_http_error(self, mock_config, mock_http_client):
        err_resp = MagicMock()
        err_resp.status_code = 500
        err_resp.text = "err"
        mock_http_client.post = AsyncMock(return_value=err_resp)

        await _send_google_chat_notification(exc=ValueError("Error"))

        mock_http_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_request_exception(self, mock_config, mock_http_client):
        mock_http_client.post = AsyncMock(
            side_effect=httpx.ConnectError("connection failed", request=MagicMock())
        )

        await _send_google_chat_notification(exc=ValueError("Error"))

        mock_http_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_failure_logs_no_payload_secrets(
        self, mock_config, mock_http_client, caplog
    ):
        caplog.set_level(logging.ERROR)
        secret = "do-not-log-this-payload-string"
        mock_http_client.post = AsyncMock(side_effect=RuntimeError("transport failed"))

        await _send_google_chat_notification(msg=secret, severity="ERROR")

        combined = " ".join(caplog.messages)
        assert secret not in combined
        assert "transport_error_type=" in combined
        assert "https://" not in combined

    @pytest.mark.asyncio
    async def test_send_traceback_present(self, mock_config, mock_http_client):
        try:
            raise ValueError("Test error")
        except ValueError as exc:
            await _send_google_chat_notification(exc=exc)

        text = mock_http_client.post.call_args.kwargs["json"]["text"]
        assert "Traceback:" in text

    @pytest.mark.asyncio
    async def test_send_includes_environment_in_body(
        self, mock_config, mock_http_client
    ):
        await _send_google_chat_notification(msg="hello", severity="ERROR")
        text = mock_http_client.post.call_args.kwargs["json"]["text"]
        assert "Environment:" in text
        assert "Alert" in text
        assert "Traceback:" not in text


class TestPostGoogleChatNotification:
    @patch("app.utils.google_chat_notifications.notify._runtime_env_and_config")
    @patch("app.utils.google_chat_notifications.notify._send_google_chat_notification")
    def test_schedule_with_running_loop(self, mock_send, mock_rt):
        mock_cfg = MagicMock()
        mock_cfg.environment = Environment.uat
        mock_rt.return_value = (Environment, mock_cfg)
        mock_send.side_effect = _fake_send

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:

            async def run_test():
                post_google_chat_notification(exc=ValueError("Error"))
                await asyncio.sleep(0.01)

            loop.run_until_complete(run_test())
            mock_send.assert_called()
        finally:
            loop.close()

    @patch("app.utils.google_chat_notifications.notify._runtime_env_and_config")
    @patch("app.utils.google_chat_notifications.notify.asyncio.run")
    @patch(
        "app.utils.google_chat_notifications.notify.asyncio.get_running_loop",
        side_effect=RuntimeError("no running event loop"),
    )
    def test_schedule_no_running_loop_starts_thread_that_calls_asyncio_run(
        self, _mock_get_loop, mock_asyncio_run, mock_rt
    ):
        mock_cfg = MagicMock()
        mock_cfg.environment = Environment.uat
        mock_rt.return_value = (Environment, mock_cfg)

        mock_thread = MagicMock()

        def thread_ctor(*_a, **kwargs):
            target = kwargs.get("target")

            def start():
                if target is not None:
                    target()

            mock_thread.start = start
            return mock_thread

        with patch("threading.Thread", side_effect=thread_ctor):
            post_google_chat_notification(exc=ValueError("Error"))

        mock_asyncio_run.assert_called_once()
        coro = mock_asyncio_run.call_args[0][0]
        assert asyncio.iscoroutine(coro)

    @patch("app.utils.google_chat_notifications.notify._runtime_env_and_config")
    @patch("app.utils.google_chat_notifications.notify.logger")
    def test_schedule_error_handling(self, mock_logger, mock_rt):
        mock_cfg = MagicMock()
        mock_cfg.environment = Environment.uat
        mock_rt.return_value = (Environment, mock_cfg)

        with patch(
            "app.utils.google_chat_notifications.notify.asyncio.get_running_loop",
            side_effect=Exception("Unexpected error"),
        ):
            post_google_chat_notification(exc=ValueError("Error"))
            mock_logger.error.assert_called()

    @pytest.mark.asyncio
    @patch("app.utils.google_chat_notifications.notify._send_google_chat_notification")
    @patch("app.utils.google_chat_notifications.notify._runtime_env_and_config")
    async def test_post_queues_when_environment_local(self, mock_rt, mock_send):
        """Local env no longer skips; work is still queued like other environments."""
        mock_cfg = MagicMock()
        mock_cfg.environment = Environment.local
        mock_rt.return_value = (Environment, mock_cfg)
        mock_send.side_effect = _fake_send

        post_google_chat_notification(exc=ValueError("Error"))
        await asyncio.sleep(0.02)
        assert mock_send.called
