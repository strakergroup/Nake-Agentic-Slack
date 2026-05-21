from app.slack.file_submissions import (
    format_slack_file_option_value,
    parse_slack_file_option_value,
    slack_file_submission_payload,
    slack_file_submission_payload_from_option,
)


def test_slack_file_submission_payload_includes_size():
    payload = slack_file_submission_payload(
        file_id="F123",
        title="file.docx",
        size=1234,
    )

    assert payload == {"id": "F123", "title": "file.docx", "size": 1234}


def test_file_option_value_round_trips_size():
    value = format_slack_file_option_value("F123", 5678)

    assert value == "F123|5678"
    assert parse_slack_file_option_value(value) == ("F123", 5678)


def test_file_option_value_supports_legacy_file_id_only_values():
    assert parse_slack_file_option_value("F123") == ("F123", None)


def test_slack_file_submission_payload_from_option_parses_size():
    payload = slack_file_submission_payload_from_option(
        {"value": "F123|9012", "text": {"text": "file.docx"}}
    )

    assert payload == {"id": "F123", "title": "file.docx", "size": 9012}
