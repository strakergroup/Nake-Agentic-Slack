from app.slack.logging import init_slack_app_log


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
