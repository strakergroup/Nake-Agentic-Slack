from typing import Any

from .models import (
    SlackAccountConnectedEvent,
    ClientSignupEvent,
    ClientApprovedEvent,
    JobStatusChangedEvent,
    JobQuoteCreatedEvent,
)
from ...slack.templates.messages import (
    SlackMessage,
    SuccessfulLoginMessage,
    ClientSignupEventMessage,
    ClientApprovedEventMessage,
    JobStatusChangedEventMessage,
    JobCompletedEventMessage,
    JobCancelledEventMessage,
    JobQuotedEventMessage,
)


def get_ray_event_message(
    event_type: str, event_data: dict[str, Any]
) -> SlackMessage | None:
    """Gets the SlackMessage based on the event type. Returns None if no Slack
    message should be sent for the particular event.

    Raises:
        pydantic.ValidationError: The data format for the event type is invalid.
        ValueError: The event type is invalid.
    """
    if event_type == "ray:slack:account_connected":
        event = SlackAccountConnectedEvent.parse_obj(event_data)
        return SuccessfulLoginMessage(event.user_id, event.username)
    elif event_type == "ray:client:signup":
        event = ClientSignupEvent.parse_obj(event_data)
        return ClientSignupEventMessage(event.username)
    elif event_type == "ray:client:approved":
        event = ClientApprovedEvent.parse_obj(event_data)
        group_names = [group.label for group in event.groups]
        return ClientApprovedEventMessage(group_names)
    elif event_type == "ray:job:status_changed":
        event = JobStatusChangedEvent.parse_obj(event_data)
        match event.status.strip().upper():
            case "LEAD" | "IN_PROGRESS" | "VALIDATION" | "REFUNDED":
                return JobStatusChangedEventMessage(
                    client_id=event.client_id,
                    job_uuid=event.uuid,
                    job_id=event.id,
                    status=event.status,
                )
            case "COMPLETED":
                return JobCompletedEventMessage(
                    client_id=event.client_id, job_uuid=event.uuid, job_id=event.id
                )
            case "CANCELLED":
                return JobCancelledEventMessage(
                    client_id=event.client_id, job_uuid=event.uuid, job_id=event.id
                )
        return None
    elif event_type == "ray:job:quote_created":
        event = JobQuoteCreatedEvent.parse_obj(event_data)
        return JobQuotedEventMessage(event)
    raise ValueError(f"Invalid RAY event type: {event_type}")
