from __future__ import annotations

FILE_SUBMISSION_VALUE_SEPARATOR = "|"


def format_slack_file_option_value(file_id: str, size: int | None) -> str:
    """Encode Slack file metadata into an option value that survives modal submit."""
    if size is None:
        return file_id
    return f"{file_id}{FILE_SUBMISSION_VALUE_SEPARATOR}{size}"


def parse_slack_file_option_value(value: str) -> tuple[str, int | None]:
    """Decode a file option value into Slack file ID and optional size in bytes."""
    file_id, separator, raw_size = value.partition(FILE_SUBMISSION_VALUE_SEPARATOR)
    if not separator:
        return value, None
    if raw_size.isdigit():
        return file_id, int(raw_size)
    return file_id, None


def slack_file_submission_payload(
    *,
    file_id: str,
    title: str,
    size: int | None,
) -> dict[str, str | int | None]:
    """Build a queue-safe Slack file payload."""
    return {"id": file_id, "title": title, "size": size}


def slack_file_submission_payload_from_option(
    option: dict[str, object],
) -> dict[str, str | int | None]:
    """Build a queue-safe Slack file payload from a submitted select option."""
    raw_value = option.get("value")
    raw_text = option.get("text")
    title = raw_text.get("text") if isinstance(raw_text, dict) else None
    file_id, size = parse_slack_file_option_value(
        raw_value if isinstance(raw_value, str) else ""
    )
    return slack_file_submission_payload(
        file_id=file_id,
        title=title if isinstance(title, str) else "",
        size=size,
    )
