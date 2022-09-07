"""Slack Messages templates."""
# Ignore line too long lint errors
# flake8: noqa

from typing import Any
import json
from ray_sdk.api.v3.models import Job

from .models import NewJobForm
from .blocks import job_deltaray_link_block
from ...ray.utils import (
    get_job_url,
    format_currency,
    format_currency_symbol,
    format_job_status,
)
from ...config import config
from ...auth.connector import RayClient, get_slack_deltaray_integration_url


class TextMessage:
    """A class representing a text-only Slack Message."""

    def __init__(self, text: str) -> None:
        self._text = text

    @property
    def text(self) -> str:
        return self._text


class SlackMessage(TextMessage):
    """A class representing a Slack Message with blocks."""

    def __init__(self, text: str, blocks: list[dict[str, Any]]) -> None:
        super().__init__(text)
        self._blocks = blocks

    @property
    def blocks(self) -> list[dict[str, Any]]:
        return self._blocks


class OnboardingMessage(SlackMessage):
    """Message to send to onboard a new user."""

    def __init__(
        self, user_id: str, team_id: str, app_id: str, channel_id: str
    ) -> None:
        super().__init__(
            "The Straker App has been sucessfully installed in your Slack workspace! :tada:",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "The Straker App has been sucessfully installed in your Slack workspace! :tada:",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Connect your DeltaRAY account to get details about your translation jobs.",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "Connect DeltaRAY account",
                            },
                            "style": "primary",
                            "url": get_slack_deltaray_integration_url(
                                user_id, team_id, app_id, channel_id
                            ),
                            "action_id": "login",
                        }
                    ],
                },
            ],
        )


class LoginMessage(SlackMessage):
    """Message to send to prompt the user to connect their DeltaRAY account."""

    def __init__(
        self,
        user_id: str,
        team_id: str,
        app_id: str,
        channel_id: str,
        ray_client: RayClient | None = None,
    ) -> None:
        """Constructor for the login Slack message. If the Slack user already has
        a connected DeltaRAY account, creates a variation with the client username
        in the message.

        Args:
            user_id (str): The Slack user ID.
            team_id (str): The Slack team ID.
            app_id (str): The Slack app ID.
            channel_id (str): The Slack channel ID to send the successful login message to.
            ray_client (RayClient | None, optional): Pass the RayClient info to use a
                variation of the message. Defaults to None.
        """
        block_text = "Connect your DeltaRAY account by clicking this button."
        if isinstance(ray_client, RayClient):
            block_text = (
                f"Your connected DeltaRAY account is: <{config.deltaray_domain}|{ray_client.username}>.\n"
                "You can connect to another account by clicking this button."
            )

        super().__init__(
            "Connect your DeltaRAY account",
            [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": block_text},
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "Connect DeltaRAY account",
                            },
                            "style": "primary",
                            "url": get_slack_deltaray_integration_url(
                                user_id, team_id, app_id, channel_id
                            ),
                            "action_id": "login",
                        }
                    ],
                },
            ],
        )


class SuccessfulLoginMessage(SlackMessage):
    """Message to send after a user successfully connects their DeltaRAY
    account.
    """

    def __init__(self, user_id: str, ray_username: str) -> None:
        super().__init__(
            ":white_check_mark: Login was successful!",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":white_check_mark: Login was successful! <@{user_id}> is now connected with <{config.deltaray_domain}|{ray_username}>.",
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Here are some things to get you started*",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": "Check your job status"},
                        {"type": "mrkdwn", "text": "`/ray [TJ number]`"},
                    ],
                },
                {"type": "divider"},
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": ":question: Need more information? Ask our chat bot below.\n:tada: New features coming soon `/ray whatsnext`",
                        }
                    ],
                },
            ],
        )


class JobStatusMessage(SlackMessage):
    """Message showing the status of a translation job."""

    def __init__(self, job: Job, client_id: str) -> None:
        super().__init__(
            f"Job status ({job.id}): {format_job_status(job.status)}",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"The job status for *{job.id}* is below:",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": "*Status:*"},
                        {"type": "mrkdwn", "text": format_job_status(job.status)},
                        {"type": "mrkdwn", "text": "*Source Language:*"},
                        {"type": "mrkdwn", "text": job.sl.name},
                        {"type": "mrkdwn", "text": "*Target Language:*"},
                        {
                            "type": "mrkdwn",
                            "text": ", ".join(sorted([lang.name for lang in job.tl])),
                        },
                        {"type": "mrkdwn", "text": "*Expected Completion Date:*"},
                        {
                            "type": "mrkdwn",
                            "text": job.target_date.strftime("%Y-%m-%d %H:%M:%S UTC"),
                        },
                    ],
                },
                job_deltaray_link_block(job.uuid, client_id),
            ],
        )


class InvalidJobMessage(TextMessage):
    """The user does not have access to the job."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"Cannot find the job: *{job_id.upper()}*")


class JobStatusNoIdMessage(TextMessage):
    """Message to send when the user asks for a job status but has not given
    a TJ number.
    """

    def __init__(self) -> None:
        super().__init__(
            "To check the status of your job, type the reference number (e.g. TJ123456)."
        )


class NewJobMessage(SlackMessage):
    """Message with a button to open the new job modal."""

    def __init__(self, channel_id: str, timestamp: str) -> None:
        super().__init__(
            "Submit a new translation job",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Click here to submit a new translation job",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "New translation job",
                                "emoji": True,
                            },
                            "action_id": "new_job",
                            "style": "primary",
                            "value": json.dumps(
                                {
                                    "channel_id": channel_id,
                                    "ts": timestamp,
                                }
                            ),
                        }
                    ],
                },
            ],
        )


class JobSubmitMessage(SlackMessage):
    """Message to send when a new job is submitted."""

    def __init__(self, new_job_form: NewJobForm) -> None:
        super().__init__(
            "Your translation request has been submitted. You will be notified when a job number is assigned.",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":tada: Your translation request has been submitted. You will be notified when a job number is assigned.",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"The following files will be translated from *{new_job_form.source_lang.name}* "
                        + f"to {', '.join(f'*{lang.name}*' for lang in new_job_form.target_langs)}:",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "\n".join(
                            f"• {file.title}" for file in new_job_form.files
                        ),
                    },
                },
            ],
        )


class JobCreationMessage(TextMessage):
    """Message to send when a job TJ number is created after submitting a new job."""

    def __init__(self, job_id: str, files: list[str] | None = None) -> None:
        super().__init__(
            f"A new translation job has been created with the job number: `{job_id}`"
        )


class HelpMessage(SlackMessage):
    """Help message showing how to use the app."""

    def __init__(self) -> None:
        super().__init__(
            "Hi there :wave: here are some ideas of what you can currently do with our Beta app:",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Hi there :wave: here are some ideas of what you can currently do with our Beta app:",
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": "Check your job status"},
                        {"type": "mrkdwn", "text": "`/ray [TJ number]`"},
                        {"type": "mrkdwn", "text": "Connect your DeltaRAY account"},
                        {"type": "mrkdwn", "text": "`/ray connect`"},
                    ],
                },
                {"type": "divider"},
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": ":question: Need more information? Ask our chat bot below.\n:tada: New features coming soon `/ray whatsnext`",
                        }
                    ],
                },
            ],
        )


class WhatsNextMessage(SlackMessage):
    def __init__(self) -> None:
        url = "https://help.strakertranslations.com/hc/en-us/articles/10021384538393-Current-Upcoming-Features"
        super().__init__(
            "Click here to see the upcoming features of our Beta app",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"<{url}|Click here> to see the upcoming features of our Beta app.",
                    },
                },
            ],
        )


class WhoamiMessage(TextMessage):
    """Message showing which DeltaRAY account is currently connected."""

    def __init__(self, username: str) -> None:
        super().__init__(
            f"Your connected DeltaRAY account is: <{config.deltaray_domain}|{username}>"
        )


class InvalidCommandMessage(TextMessage):
    """Invalid /ray command."""

    def __init__(self) -> None:
        super().__init__(
            ":no_entry_sign: Invalid command. Type `/ray help` for a list of valid commands."
        )


# -----------------------------------------------------------------------------
# Ray event messages
# -----------------------------------------------------------------------------


class JobStatusChangeEventMessage(SlackMessage):
    def __init__(self, client_id: str, job_data: dict[str, Any]) -> None:
        job_id = job_data["id"]
        job_uuid = job_data["uuid"]
        status = job_data["status"]
        super().__init__(
            f"Your translation job {job_id} has changed status to: {status}",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Your translation job {job_id} has changed status to: {status}",
                    },
                },
                job_deltaray_link_block(job_uuid, client_id),
            ],
        )


class JobCompletedEventMessage(SlackMessage):
    def __init__(self, client_id: str, job_data: dict[str, Any]) -> None:
        job_id = job_data["id"]
        job_uuid = job_data["uuid"]
        super().__init__(
            f":tada: Your translation job {job_id} is completed!",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":tada: Your translation job {job_id} is completed!",
                    },
                },
                job_deltaray_link_block(job_uuid, client_id),
            ],
        )


class JobCancelledEventMessage(SlackMessage):
    def __init__(self, client_id: str, job_data: dict[str, Any]) -> None:
        job_id = job_data["id"]
        job_uuid = job_data["uuid"]
        super().__init__(
            f"Your translation job {job_id} has been cancelled",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Your translation job {job_id} has been cancelled",
                    },
                },
                job_deltaray_link_block(job_uuid, client_id),
            ],
        )


class JobQuotedEventMessage(SlackMessage):
    def __init__(self, client_id: str, quote_data: dict[str, Any]) -> None:
        job: dict[str, Any] = quote_data["job"]
        job_id = job["id"]
        job_uuid = job["uuid"]
        source_lang = job["sl"]["label"]
        target_lang = job["tl"]["label"]
        turnaround = job["turnaround"]
        service = job["service"]
        quote = quote_data["quote"]
        currency = format_currency_symbol(quote_data["currency"])
        quote_formatted = format_currency(quote, quote_data["currency"])
        url = get_job_url(job_uuid, client_id)
        super().__init__(
            f"Your quote is now ready 🙌\n*<{url}|Straker Job Reference {job_id}>*",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Your quote is now ready 🙌\n*<{url}|Straker Job Reference {job_id}>*",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Source Language:*\n{source_lang}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Turnaround time:*\n{turnaround}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Service:*\n{service}",
                        },
                        {"type": "mrkdwn", "text": f"*Job Reference:*\n{job_id}"},
                    ],
                },
                {"type": "divider"},
                {
                    "type": "section",
                    # TODO: multiple target languages
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*{target_lang}:*\n{quote_formatted}",
                        },
                    ],
                },
                {"type": "divider"},
                {
                    "type": "section",
                    # TODO: format currency
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Total Cost ({currency})*: {quote_formatted}",
                    },
                },
                {"type": "divider"},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "emoji": True,
                                "text": "Accept Quote",
                            },
                            "style": "primary",
                            "url": quote_data["quote_accept_url"],
                            "action_id": "link",
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "emoji": True,
                                "text": "Cancel",
                            },
                            "style": "danger",
                            "url": quote_data["quote_cancel_url"],
                            "action_id": "link_1",
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "More Information",
                                "emoji": True,
                            },
                            "url": quote_data["quote_detail_url"],
                            "action_id": "link_2",
                        },
                    ],
                },
            ],
        )
