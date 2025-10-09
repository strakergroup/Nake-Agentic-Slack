from typing import Any

from app.api.verify import get_evaluation_job, get_job_pricing
from app.auth.connector import SlackUser, get_ray_client
from app.constants import HUMAN_EVALUATION_WORKFLOW_UUID
from app.ray.submissions import SubmissionStatus, updated_submission_status
from app.ray.utils import is_ibm_enterprise
from app.slack.select_options import _get_languages_cached

from ...slack.templates.messages import (
    ClientApprovedEventMessage,
    ClientSignupEventMessage,
    DocMtMessage,
    EvaluateSuccessMessage,
    HumanJobQuoteMessage,
    JobCancelledEventMessage,
    JobCompletedEventMessage,
    JobQuoteAcceptedEventMessage,
    JobQuoteCancelledEventMessage,
    JobQuotedEventMessage,
    JobStatusChangedEventMessage,
    JobTranscribedEventMessage,
    SlackMessage,
    SuccessfulLoginMessage,
    VerifyCompleteMessage,
)
from .models import (
    ClientApprovedEvent,
    ClientSignupEvent,
    JobQuoteAcceptedEvent,
    JobQuoteCancelledEvent,
    JobQuoteCreatedEvent,
    JobStatusChangedEvent,
    JobTranscribedEvent,
    MtFileReponseSchema,
    SlackAccountConnectedEvent,
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
    elif event_type == "transcription:slack:media:results":
        event7 = JobTranscribedEvent.model_validate(event_data)
        # send message which contains event.output_file
        return JobTranscribedEventMessage(event7.task_uuid, event7.source_file_name)
    elif event_type == "verify:slack:document:translated":
        event8 = MtFileReponseSchema.model_validate(event_data)
        updated_submission_status(
            submission_id=event8.root.submission_id,
            processing_status=SubmissionStatus.COMPLETED,
        )
        return DocMtMessage()
    elif event_type == "verify:slack:evaluate:complete":
        # fetch the job report from event_data
        # create message which displays the job report
        if event_data.get("error"):
            return DocMtMessage()
        # TODO type job
        job = await get_evaluation_job(slack_user, event_data["job_uuid"])
        if job["data"].get("human_job_in_progress", False):
            raise ValueError(f"Invalid RAY event type: {event_type}")
        all_langs = await _get_languages_cached()
        if job["data"]["workflow_uuid"] == HUMAN_EVALUATION_WORKFLOW_UUID:
            costs = await get_job_pricing(
                await get_ray_client(slack_user.user_id, slack_user.team_id),
                job["data"]["uuid"],
                [file["file_uuid"] for file in job["data"]["source_files"]],
                [lang["uuid"] for lang in job["data"]["target_languages"]],
            )
            return HumanJobQuoteMessage(job["data"], costs["data"])
        return EvaluateSuccessMessage(job["data"], is_ibm, event_data["tokens"])
    elif event_type == "verify:human_verification:completed":
        all_langs = await _get_languages_cached()
        lang_label = ""
        for lang in all_langs:
            if lang["uuid"] == event_data["lang_uuid"]:
                lang_label = lang["name"]
                break
        return VerifyCompleteMessage(event_data["job_title"], lang_label)
    raise ValueError(f"Invalid RAY event type: {event_type}")
