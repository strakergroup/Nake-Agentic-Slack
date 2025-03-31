import json
from typing import Any

from app.api.verify import get_evaluation_job
from app.auth.connector import SlackUser
from app.ray.utils import is_ibm_enterprise
from app.slack.select_options import _get_languages_cached

from .models import (
    MtFileReponseSchema,
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
    DocMtMessage,
    EvaluateSuccessMessage,
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
    VerifyCompleteMessage,
)


async def get_ray_event_message(
    event_type: str,
    event_data: dict[str, Any],
    slack_user: SlackUser | None,
    ray_connection: Any | None = None,
) -> SlackMessage | None:
    """Gets the SlackMessage based on the event type. Returns None if no Slack
    message should be sent for the particular event.

    Raises:
        pydantic.ValidationError: The data format for the event type is invalid.
        ValueError: The event type is invalid.
    """
    is_ibm = False
    if slack_user:
        is_ibm = is_ibm_enterprise(slack_user.enterprise_id)
    if event_type == "ray:slack:account_connected":
        event0 = SlackAccountConnectedEvent.model_validate(event_data)
        return SuccessfulLoginMessage(event0.user_id, event0.username, ray_connection)
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
                    is_ibm=is_ibm,
                )
            case "COMPLETED":
                return JobCompletedEventMessage(
                    client_id=event3.client_id,
                    job_uuid=event3.uuid,
                    job_id=event3.id,
                    target_languages=[lang.label for lang in event3.tl],
                    is_ibm=is_ibm,
                )
            case "CANCELLED":
                return JobCancelledEventMessage(
                    client_id=event3.client_id,
                    job_uuid=event3.uuid,
                    job_id=event3.id,
                )
        return None
    elif event_type == "ray:job:quote_created":
        event4 = JobQuoteCreatedEvent.model_validate(event_data)
        return JobQuotedEventMessage(event4, is_ibm)
    elif event_type == "ray:job:quote_accepted":
        event5 = JobQuoteAcceptedEvent.model_validate(event_data)
        return JobQuoteAcceptedEventMessage(event5, is_ibm)
    elif event_type == "ray:job:quote_cancelled":
        event6 = JobQuoteCancelledEvent.model_validate(event_data)
        return JobQuoteCancelledEventMessage(
            client_id=event6.client_id,
            job_uuid=event6.uuid,
            job_id=event6.id,
            is_ibm=is_ibm,
        )
    elif event_type == "ray:job:transcribed":
        event7 = JobTranscribedEvent.model_validate(event_data)
        # send message which contains event.output_file
        return JobTranscribedEventMessage(event7.task_uuid)
    elif event_type == "verify:slack:document:translated":
        event8 = MtFileReponseSchema.model_validate(event_data)
        return DocMtMessage()
    elif event_type == "verify:slack:evaluate:complete":
        # fetch the job report from event_data
        # create message which displays the job report
        if event_data.get("error"):
            return DocMtMessage()
        # TODO type job
        job = await get_evaluation_job(slack_user, event_data["job_uuid"])
        if job["data"]["workflow_uuid"] == "92741a61-932c-41af-8c84-5a56a2c9b845":
            return None
        all_langs = await _get_languages_cached()
        return EvaluateSuccessMessage(
            job["data"], all_langs, event_data["tokens"], is_ibm
        )
    elif event_type == "verify:human_verification:completed":
        all_langs = await _get_languages_cached()
        lang_label = ""
        for lang in all_langs:
            if lang["uuid"] == event_data["lang_uuid"]:
                lang_label = lang["name"]
                break
        return VerifyCompleteMessage(event_data["job_title"], lang_label)
    raise ValueError(f"Invalid RAY event type: {event_type}")
