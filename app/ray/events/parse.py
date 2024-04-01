from typing import Any

from .models import (
    SlackAccountConnectedEvent,
    ClientSignupEvent,
    ClientApprovedEvent,
    JobStatusChangedEvent,
    JobQuoteCreatedEvent,
    JobQuoteAcceptedEvent,
    JobQuoteCancelledEvent,
    JobTranscribedEvent,
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
    JobQuoteAcceptedEventMessage,
    JobQuoteCancelledEventMessage,
    JobTranscribedEventMessage,
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
        event0 = SlackAccountConnectedEvent.model_validate(event_data)
        return SuccessfulLoginMessage(event0.user_id, event0.username)
    elif event_type == "ray:client:signup":
        event1 = ClientSignupEvent.model_validate(event_data)
        return ClientSignupEventMessage(event1)
    elif event_type == "ray:client:approved":
        event2 = ClientApprovedEvent.model_validate(event_data)
        group_names = [group.label for group in event2.groups]
        return ClientApprovedEventMessage(group_names)
    elif event_type == "ray:job:status_changed":
        event3 = JobStatusChangedEvent.model_validate(event_data)
        event3.status = event3.status.strip().upper()
        event3.previous_status = (
            event3.previous_status.strip().upper() if event3.previous_status else None
        )
        # Do not send notification if quote is accepted or cancelled,
        # send those notifications instead.
        if (
            event3.status in ("IN_PROGRESS", "CANCELLED")
            and event3.previous_status == "LEAD"
        ):
            return None
        match event3.status:
            case "LEAD" | "IN_PROGRESS" | "VALIDATION" | "REFUNDED":
                return JobStatusChangedEventMessage(
                    client_id=event3.client_id,
                    job_uuid=event3.uuid,
                    job_id=event3.id,
                    status=event3.status,
                )
            case "COMPLETED":
                return JobCompletedEventMessage(
                    client_id=event3.client_id,
                    job_uuid=event3.uuid,
                    job_id=event3.id,
                    target_languages=[lang.label for lang in event3.tl],
                )
            case "CANCELLED":
                return JobCancelledEventMessage(
                    client_id=event3.client_id, job_uuid=event3.uuid, job_id=event3.id
                )
        return None
    elif event_type == "ray:job:quote_created":
        event4 = JobQuoteCreatedEvent.model_validate(event_data)
        return JobQuotedEventMessage(event4)
    elif event_type == "ray:job:quote_accepted":
        event5 = JobQuoteAcceptedEvent.model_validate(event_data)
        return JobQuoteAcceptedEventMessage(event5)
    elif event_type == "ray:job:quote_cancelled":
        event6 = JobQuoteCancelledEvent.model_validate(event_data)
        return JobQuoteCancelledEventMessage(
            client_id=event6.client_id, job_uuid=event6.uuid, job_id=event6.id
        )
    elif event_type == "ray:job:transcribed":
        event7 = JobTranscribedEvent.model_validate(event_data)
        # send message which contains event.output_file
        return JobTranscribedEventMessage(event7.output_file)

    raise ValueError(f"Invalid RAY event type: {event_type}")
