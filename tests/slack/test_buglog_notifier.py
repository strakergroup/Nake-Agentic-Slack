"""Tests for app.slack.buglog_notifier module."""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

from app.utils.google_chat_notify import GoogleChatContext

_ROOT = Path(__file__).resolve().parents[2]


def _load_buglog_notifier():
    """Load ``buglog_notifier`` without executing ``app.slack`` package ``__init__``."""
    name = "app.slack.buglog_notifier"
    if name in sys.modules:
        return sys.modules[name]
    if "app" not in sys.modules:
        app_m = types.ModuleType("app")
        app_m.__path__ = [str(_ROOT / "app")]
        sys.modules["app"] = app_m
    if "app.slack" not in sys.modules:
        slack_m = types.ModuleType("app.slack")
        slack_m.__path__ = [str(_ROOT / "app" / "slack")]
        sys.modules["app.slack"] = slack_m
    path = _ROOT / "app" / "slack" / "buglog_notifier.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_bn = _load_buglog_notifier()
notify_exception = _bn.notify_exception
notify_message = _bn.notify_message


class TestNotifyException:
    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_exception_calls_buglog(self, mock_schedule, mock_buglog_notify):
        mock_buglog_notify.return_value = True
        exc = ValueError("Test error")

        result = notify_exception(exc, "Test message", {"key": "value"}, "ERROR")

        mock_buglog_notify.assert_called_once_with(
            exc, "Test message", {"key": "value"}, "ERROR"
        )
        assert result is True

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_exception_schedules_chat(self, mock_schedule, mock_buglog_notify):
        mock_buglog_notify.return_value = True
        exc = ValueError("Test error")

        notify_exception(exc, "Test message", {"key": "value"}, "ERROR")

        mock_schedule.assert_called_once_with(
            exc,
            "Test message",
            {"key": "value"},
            "ERROR",
            context=GoogleChatContext.BUGLOG,
        )

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_exception_buglog_false_still_schedules_chat(
        self, mock_schedule, mock_buglog_notify
    ):
        mock_buglog_notify.return_value = False
        exc = ValueError("x")
        result = notify_exception(exc, "m", None, "ERROR")
        assert result is False
        mock_schedule.assert_called_once_with(
            exc, "m", None, "ERROR", context=GoogleChatContext.BUGLOG
        )

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_exception_with_message_only(
        self, mock_schedule, mock_buglog_notify
    ):
        mock_buglog_notify.return_value = True

        notify_exception(msg="Test message")

        mock_schedule.assert_called_once_with(
            None,
            "Test message",
            None,
            "ERROR",
            context=GoogleChatContext.BUGLOG,
        )

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_exception_no_exception_no_message(
        self, mock_schedule, mock_buglog_notify
    ):
        mock_buglog_notify.return_value = True

        notify_exception()

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_not_called()

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_exception_whitespace_only_message_skips_chat_mirror(
        self, mock_schedule, mock_buglog_notify
    ):
        mock_buglog_notify.return_value = True

        notify_exception(msg="   \t  ")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_not_called()


class TestNotifyMessage:
    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_message_calls_buglog(self, mock_schedule, mock_buglog_notify):
        mock_buglog_notify.return_value = True

        result = notify_message("Test message", {"key": "value"}, "INFO")

        mock_buglog_notify.assert_called_once_with(
            "Test message", {"key": "value"}, "INFO"
        )
        assert result is True

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_message_schedules_chat_for_error(
        self, mock_schedule, mock_buglog_notify
    ):
        mock_buglog_notify.return_value = True

        notify_message("Test message", {"key": "value"}, "ERROR")

        mock_schedule.assert_called_once_with(
            None,
            "Test message",
            {"key": "value"},
            "ERROR",
            context=GoogleChatContext.BUGLOG,
        )

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_message_empty_message(self, mock_schedule, mock_buglog_notify):
        mock_buglog_notify.return_value = True

        notify_message("")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_not_called()

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_message_whitespace_only_skips_chat_mirror(
        self, mock_schedule, mock_buglog_notify
    ):
        mock_buglog_notify.return_value = True

        notify_message("  \n  ")

        mock_buglog_notify.assert_called_once_with("  \n  ", None, "INFO")
        mock_schedule.assert_not_called()

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_message_default_severity(self, mock_schedule, mock_buglog_notify):
        mock_buglog_notify.return_value = True

        notify_message("Test message")

        mock_buglog_notify.assert_called_once_with("Test message", None, "INFO")
        mock_schedule.assert_called_once_with(
            None,
            "Test message",
            None,
            "INFO",
            context=GoogleChatContext.BUGLOG,
        )

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_message_info_severity_mirrors_chat(
        self, mock_schedule, mock_buglog_notify
    ):
        mock_buglog_notify.return_value = True

        notify_message("Test message", severity="INFO")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_called_once_with(
            None,
            "Test message",
            None,
            "INFO",
            context=GoogleChatContext.BUGLOG,
        )

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_message_error_severity_sends(
        self, mock_schedule, mock_buglog_notify
    ):
        mock_buglog_notify.return_value = True

        notify_message("Test error message", severity="ERROR")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_called_once_with(
            None,
            "Test error message",
            None,
            "ERROR",
            context=GoogleChatContext.BUGLOG,
        )

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_message_warning_severity_sends(
        self, mock_schedule, mock_buglog_notify
    ):
        mock_buglog_notify.return_value = True

        notify_message("Test warning message", severity="WARNING")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_called_once_with(
            None,
            "Test warning message",
            None,
            "WARNING",
            context=GoogleChatContext.BUGLOG,
        )

    @patch("app.slack.buglog_notifier.buglog_notify_message")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_message_fatal_severity_sends(
        self, mock_schedule, mock_buglog_notify
    ):
        mock_buglog_notify.return_value = True

        notify_message("Test fatal message", severity="FATAL")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_called_once_with(
            None,
            "Test fatal message",
            None,
            "FATAL",
            context=GoogleChatContext.BUGLOG,
        )

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_exception_info_severity_mirrors_chat(
        self, mock_schedule, mock_buglog_notify
    ):
        mock_buglog_notify.return_value = True
        exc = ValueError("Test error")

        notify_exception(exc, severity="INFO")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_called_once_with(
            exc, None, None, "INFO", context=GoogleChatContext.BUGLOG
        )

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_exception_warning_severity_sends(
        self, mock_schedule, mock_buglog_notify
    ):
        mock_buglog_notify.return_value = True
        exc = ValueError("Test error")

        notify_exception(exc, severity="WARNING")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_called_once_with(
            exc, None, None, "WARNING", context=GoogleChatContext.BUGLOG
        )

    @patch("app.slack.buglog_notifier.buglog_notify_exception")
    @patch("app.utils.google_chat_notify.post_google_chat_notification")
    def test_notify_exception_fatal_severity_sends(
        self, mock_schedule, mock_buglog_notify
    ):
        mock_buglog_notify.return_value = True
        exc = ValueError("Test error")

        notify_exception(exc, severity="FATAL")

        mock_buglog_notify.assert_called_once()
        mock_schedule.assert_called_once_with(
            exc, None, None, "FATAL", context=GoogleChatContext.BUGLOG
        )
