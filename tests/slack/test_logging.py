from app.slack.logging import get_memory_mb, init_slack_app_log


class TestGetMemoryMb:
    """Tests for get_memory_mb function."""

    def test_get_memory_mb_returns_float(self):
        """Test that get_memory_mb returns a float."""
        memory = get_memory_mb()
        assert isinstance(memory, float)
        assert memory >= 0

    def test_get_memory_mb_positive_value(self):
        """Test that get_memory_mb returns a positive value."""
        memory = get_memory_mb()
        assert memory > 0


class TestInitSlackLog:
    def test_event(self, event_body, context):
        log = init_slack_app_log(event_body, context)
        assert log.slack_log.action_type == "event"
        assert log.slack_log.action_value == event_body["event"]["type"]
        assert log.slack_log.ts == event_body["event"]["event_ts"]
        assert log.slack_log.user_id == context["user_id"]
        assert log.slack_log.team_id == context["team_id"]
        assert log.slack_log.channel_id == context["channel_id"]

    def test_event_subtype(self, file_share_event_body, context):
        body = file_share_event_body  # Alias to reduce line size
        log = init_slack_app_log(body, context)
        assert log.slack_log.action_type == "event"
        assert (
            log.slack_log.action_value
            == f"{body['event']['type']}:{body['event']['subtype']}"
        )
        assert log.slack_log.ts == body["event"]["event_ts"]
        assert log.slack_log.user_id == context["user_id"]
        assert log.slack_log.team_id == context["team_id"]
        assert log.slack_log.channel_id == context["channel_id"]

    def test_block_action(self, block_action_body, context):
        log = init_slack_app_log(block_action_body, context)
        assert log.slack_log.action_type == "block_action"
        assert (
            log.slack_log.action_value == block_action_body["actions"][0]["action_id"]
        )
        assert log.slack_log.ts == block_action_body["actions"][0]["action_ts"]
        assert log.slack_log.user_id == context["user_id"]
        assert log.slack_log.team_id == context["team_id"]
        assert log.slack_log.channel_id == context["channel_id"]

    def test_slash_command(self, command_body, context):
        log = init_slack_app_log(command_body, context)
        assert log.slack_log.action_type == "command"
        assert log.slack_log.action_value == (
            f"{command_body['command']} {command_body['text']}"
            if "text" in command_body
            else command_body["command"]
        )
        assert log.slack_log.ts is None
        assert log.slack_log.user_id == context["user_id"]
        assert log.slack_log.team_id == context["team_id"]
        assert log.slack_log.channel_id == context["channel_id"]

    def test_options(self, context):
        """Test options action type."""
        body = {"type": "block_suggestion", "action_id": "test_action"}
        log = init_slack_app_log(body, context)
        assert log.slack_log.action_type == "options"
        assert log.slack_log.action_value == "test_action"
        assert log.slack_log.ts is None

    def test_global_shortcut(self, context):
        """Test global shortcut action type."""
        body = {
            "type": "shortcut",
            "callback_id": "test_shortcut",
            "action_ts": "123456.789",
        }
        log = init_slack_app_log(body, context)
        assert log.slack_log.action_type == "global_shortcut"
        assert log.slack_log.action_value == "test_shortcut"
        assert log.slack_log.ts == "123456.789"

    def test_message_shortcut(self, context):
        """Test message shortcut action type."""
        body = {
            "type": "message_action",
            "callback_id": "test_message_shortcut",
            "action_ts": "123456.789",
        }
        log = init_slack_app_log(body, context)
        assert log.slack_log.action_type == "message_shortcut"
        assert log.slack_log.action_value == "test_message_shortcut"
        assert log.slack_log.ts == "123456.789"

    def test_view_submission(self, context):
        """Test view submission action type."""
        body = {"type": "view_submission", "view": {"callback_id": "test_view"}}
        log = init_slack_app_log(body, context)
        assert log.slack_log.action_type == "view_submission"
        assert log.slack_log.action_value == "test_view"
        assert log.slack_log.ts is None

    def test_view_closed(self, context):
        """Test view closed action type."""
        body = {"type": "view_closed", "view": {"callback_id": "test_view_closed"}}
        log = init_slack_app_log(body, context)
        assert log.slack_log.action_type == "view_closed"
        assert log.slack_log.action_value == "test_view_closed"
        assert log.slack_log.ts is None

    def test_unknown_action_type(self, context):
        """Test unknown action type returns None values."""
        body = {"unknown": "data"}
        log = init_slack_app_log(body, context)
        assert log.slack_log.action_type is None
        assert log.slack_log.action_value is None
        assert log.slack_log.ts is None
