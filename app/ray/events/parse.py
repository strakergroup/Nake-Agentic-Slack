from typing import Any

from .models import SlackAccountConnectedEvent
from ...slack.templates.messages import (
    SlackMessage,
    SuccessfulLoginMessage,
    JobStatusChangeEventMessage,
    JobCompletedEventMessage,
    JobCancelledEventMessage,
    JobQuotedEventMessage,
)


def get_ray_event_message(event_type: str, event_data: dict[str, Any]) -> SlackMessage:
    """Gets the SlackMessage based on the event type.

    Raises:
        pydantic.ValidationError: The data format for the event type is invalid.
        ValueError: The event type is invalid.
    """
    if event_type == "ray:slack:account_connected":
        event = SlackAccountConnectedEvent.parse_obj(event_data)
        return SuccessfulLoginMessage(event.user_id, event.username)
    # elif event_type == "ray:job:status_changed":
    #     pass
    # elif event_type == "ray:job:quote_created":
    #     pass
    elif event_type == "job_status":
        return JobStatusChangeEventMessage(event_data.get("client_id", ""), event_data)
    elif event_type == "job_completed":
        return JobCompletedEventMessage(event_data.get("client_id", ""), event_data)
    elif event_type == "job_cancelled":
        return JobCancelledEventMessage(event_data.get("client_id", ""), event_data)
    elif event_type == "quote_created":
        return JobQuotedEventMessage(event_data.get("client_id", ""), event_data)
    raise ValueError(f"Invalid RAY event type: {event_type}")
