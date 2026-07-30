"""Tests for Slack response timestamp extraction."""

from app.ray.events.logging import slack_response_message_ts


def test_slack_response_message_ts_from_dict():
    assert slack_response_message_ts({"ts": "111.222"}) == "111.222"


def test_slack_response_message_ts_from_sdk_like_object():
    class SlackResponse:
        def get(self, key, default=None):
            return {"ts": "333.444"}.get(key, default)

    assert slack_response_message_ts(SlackResponse()) == "333.444"


def test_slack_response_message_ts_rejects_non_string_and_missing():
    assert slack_response_message_ts(None) is None
    assert slack_response_message_ts({}) is None
    assert slack_response_message_ts({"ts": 123}) is None
    assert slack_response_message_ts(object()) is None
