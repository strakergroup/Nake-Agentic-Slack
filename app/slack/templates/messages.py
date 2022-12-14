"""Slack Messages templates."""
# Ignore line too long lint errors
# flake8: noqa

from typing import Any
import datetime
import json
from ray_sdk.api.v3.models import Job, Pagination

from .models import NewJobForm
from .blocks import job_deltaray_link_block
from ...ray.events.models import ClientSignupEvent, JobQuoteCreatedEvent, ClientGroup
from ...ray.utils import (
    get_job_url,
    format_currency,
    format_currency_symbol,
    format_job_status,
    format_job_due_date_slack,
)
from ...config import domains
from ...auth.connector import (
    RayClient,
    RayConnection,
    get_slack_deltaray_integration_url,
)


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

    GET_JOB = "get_job"
    NEW_JOB = "new_job"

    def __init__(
        self,
        user_id: str,
        team_id: str,
        app_id: str,
        channel_id: str,
        ray_client: RayClient | None = None,
        variation: str | None = None,
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
            variation (str | None, optional): The variation of the message to use.
                The options are in the class variables. Defaults to None.
        """
        self._user_id = user_id
        self._team_id = team_id
        self._app_id = app_id
        self._channel_id = channel_id
        self._ray_client = ray_client
        self._variation = variation

        # Have variations of the login message depending on the arguments.
        block_text = "Click this button to connect your DeltaRAY account."
        if variation == self.GET_JOB:
            block_text = "Connect your DeltaRAY account to view your jobs."
        elif variation == self.NEW_JOB:
            block_text = (
                "Connect your DeltaRAY account to submit a new translation job."
            )
        elif isinstance(ray_client, RayClient):
            block_text = (
                f"Your connected DeltaRAY account is: <{domains.deltaray}|{ray_client.username}>.\n"
                "You can connect a different account by clicking this button."
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

    def with_variation(self, variation: str | None) -> "LoginMessage":
        """Returns a copy of this message with a different variation. If the
        variation selected is the same as the current variation, just returns
        the current instance.
        """
        if self._variation == variation:
            return self
        return LoginMessage(
            user_id=self._user_id,
            team_id=self._team_id,
            app_id=self._app_id,
            channel_id=self._channel_id,
            ray_client=self._ray_client,
            variation=variation,
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
                        "text": f":white_check_mark: Login was successful! <@{user_id}> is now connected with <{domains.deltaray}|{ray_username}>.",
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
                        {"type": "mrkdwn", "text": "`/ray [job reference]`"},
                        {"type": "mrkdwn", "text": "Your daily summary"},
                        {"type": "mrkdwn", "text": "`/ray my jobs`"},
                        {
                            "type": "mrkdwn",
                            "text": "Upload files to translate and submit a quote request",
                        },
                        {"type": "mrkdwn", "text": "`/ray new`"},
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


class LogoutMessage(SlackMessage):
    """Message with a button disconnect a user's DeltaRAY account."""

    def __init__(self, ray_username: str) -> None:
        super().__init__(
            "Disconnect your DeltaRAY account",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Click this button to disconnect your DeltaRAY account: <{domains.deltaray}|{ray_username}>.",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "Disconnect DeltaRAY account",
                            },
                            "style": "danger",
                            "action_id": "disconnect",
                            "value": ray_username,
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "Hide this message",
                            },
                            "action_id": "delete_ephemeral_message",
                        },
                    ],
                },
            ],
        )


class SuccessfulLogoutMessage(SlackMessage):
    """A Slack user's DeltaRAY account was successfully disconnected."""

    def __init__(self, user_id: str, ray_username: str | None = None) -> None:
        block_message = (
            f"Your DeltaRAY account <{domains.deltaray}|{ray_username}> is now disconnected from <@{user_id}>."
            if ray_username
            else f"Your DeltaRAY account is now disconnected from <@{user_id}>."
        )
        super().__init__(
            "Your DeltaRAY account is now disconnected.",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": block_message,
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "You can use `/ray connect` to connect your DeltaRAY account again.",
                    },
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
                            "text": format_job_due_date_slack(
                                job.target_date, job.status, traffic_light=True
                            ),
                        },
                    ],
                },
                job_deltaray_link_block(job.uuid, client_id),
            ],
        )


class JobDetailsMessage(SlackMessage):
    """Message showing the details of a translation job."""

    def __init__(self, job: Job, client_id: str) -> None:
        super().__init__(
            f"The information for {job.id} is below:",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"The information for <{get_job_url(job.uuid, client_id)}|*{job.id}*> is below:",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Job Status:*\n{format_job_status(job.status)}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Group:*\n{job.group.name if job.group else ''}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Due Date/Time*\n{format_job_due_date_slack(job.target_date, job.status, traffic_light=True)}",
                        },
                        {"type": "mrkdwn", "text": f"*Reference:*\n{job.reference}"},
                        {
                            "type": "mrkdwn",
                            "text": f"*Source Language:*\n{job.sl.name}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Target Languages:*\n{', '.join(sorted([lang.name for lang in job.tl]))}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Valdation*\n{'Yes' if job.validation else 'No'}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Project Manager*\n<mailto:{job.project_manager.email}|{job.project_manager.first_name} {job.project_manager.last_name}>",
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


class JobSummaryMessage(SlackMessage):
    """A summary of the client's jobs, number of jobs in each status. Has buttons
    to display the individual job IDs for each status and timeframe.
    """

    def __init__(
        self,
        in_progress: int,
        completed: int,
        validation: int,
        pending_quotes: int,
        order_now: int,
    ) -> None:
        """The constructor.

        Args:
            in_progress (int): The total number of jobs in progress.
            completed (int): The number of jobs completed in the past 7 days.
            validation (int): The total number of jobs in validation.
            pending_quotes (int): The total number of pending quotes.
            order_now (int): The total number of jobs ready to order.
        """
        super().__init__(
            f"In Progress Jobs: {in_progress} jobs currently in progress...",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*In Progress Jobs*\n{in_progress} job(s) currently in progress",
                    },
                    "accessory": {
                        "type": "static_select",
                        "action_id": "job_list",
                        "placeholder": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "Options",
                        },
                        "options": [
                            {
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": "Jobs accepted within the last 24 hours",
                                },
                                "value": "IN_PROGRESS:ACCEPTED:24H",
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": "Jobs due within the next 24 hours",
                                },
                                "value": "IN_PROGRESS:DUE:24H",
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": "All jobs in progress",
                                },
                                "value": "IN_PROGRESS",
                            },
                        ],
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Completed Jobs*\n{completed} job(s) completed in the past 7 days",
                    },
                    "accessory": {
                        "type": "static_select",
                        "action_id": "job_list",
                        "placeholder": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "Options",
                        },
                        "options": [
                            {
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": "Jobs completed within the last 24 hours",
                                },
                                "value": "COMPLETED:24H",
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": "Jobs completed within the last 48 hours",
                                },
                                "value": "COMPLETED:48H",
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": "Jobs completed within the last 7 days",
                                },
                                "value": "COMPLETED:7D",
                            },
                        ],
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Validation*\n{validation} job(s) currently being validated",
                    },
                    "accessory": {
                        "type": "static_select",
                        "action_id": "job_list",
                        "placeholder": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "Options",
                        },
                        "options": [
                            {
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": "All jobs in validation",
                                },
                                "value": "VALIDATION",
                            }
                        ],
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Pending Quotes*\n{pending_quotes} quote(s) pending",
                    },
                    "accessory": {
                        "type": "static_select",
                        "action_id": "job_list",
                        "placeholder": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "Options",
                        },
                        "options": [
                            {
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": "Pending quotes from the last 24 hours",
                                },
                                "value": "PENDING_QUOTES:24H",
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": "All pending quotes",
                                },
                                "value": "PENDING_QUOTES",
                            },
                        ],
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Order Now*\n{order_now} job(s) to order",
                    },
                    "accessory": {
                        "type": "static_select",
                        "action_id": "job_list",
                        "placeholder": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "Options",
                        },
                        "options": [
                            {
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": "Jobs quoted from the last 24 hours",
                                },
                                "value": "ORDER_NOW:24H",
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": "Jobs quoted from the last 7 days",
                                },
                                "value": "ORDER_NOW:7D",
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": "All jobs quoted",
                                },
                                "value": "ORDER_NOW",
                            },
                        ],
                    },
                },
            ],
        )


class JobListMessage(SlackMessage):
    """A list of the client's jobs."""

    def __init__(
        self,
        preset: str,
        title: str,
        jobs: list[Job],
        pagination: Pagination,
        client_ref: str = "",
    ) -> None:
        jobs_blocks = []
        if jobs:
            for i, job in enumerate(jobs):
                job_text = f"*{job.id}*"
                if job.reference:
                    job_text += f"\nRef: {job.reference}"
                job_text += f"\n{job.sl.shortname.upper()} > {', '.join(lang.shortname.upper() for lang in job.tl)}"
                job_text += "\nDue: " + format_job_due_date_slack(
                    job.target_date, job.status, traffic_light=True
                )
                jobs_blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": job_text,
                        },
                        "accessory": {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "emoji": True,
                                "text": "View More Info",
                            },
                            "action_id": "show_job_details",
                            "value": job.id,
                        },
                    }
                )
        else:
            jobs_blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"No jobs found",
                    },
                }
            )

        pagination_blocks = []
        if jobs and pagination.total_pages > 1:
            pagination_blocks.append({"type": "actions", "elements": []})
            if pagination.page > 1:
                pagination_blocks[0]["elements"].append(
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Show previous jobs",
                            "emoji": True,
                        },
                        "action_id": "job_list_paginated_0",
                        "value": json.dumps(
                            {
                                "preset": preset,
                                "client_reference": client_ref[:1000],
                                "page": pagination.page - 1,
                                "page_size": pagination.rows_per_page,
                            }
                        ),
                    }
                )
            if pagination.page < pagination.total_pages:
                pagination_blocks[0]["elements"].append(
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Show more jobs",
                            "emoji": True,
                        },
                        "action_id": "job_list_paginated_1",
                        "value": json.dumps(
                            {
                                "preset": preset,
                                "client_reference": client_ref[:1000],
                                "page": pagination.page + 1,
                                "page_size": pagination.rows_per_page,
                            }
                        ),
                    }
                )

        super().__init__(
            title,
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{title}*",
                    },
                },
                *jobs_blocks,
                *pagination_blocks,
            ],
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
    """A job TJ number is created after submitting a new job (from API v3 callback)."""

    def __init__(self, job_id: str) -> None:
        super().__init__(
            f"A new translation job has been created with the job number: `{job_id}`"
        )


class FileTranslatedMessage(SlackMessage):
    """A file is translated can be downloaded (from API v3 callback)."""

    def __init__(
        self,
        job_id: str,
        source_file: str,
        source_lang: str,
        files: list[dict[str, str]],
    ) -> None:
        """
        Args:
            job_id (str): The job ID.
            source_file (str): The name of the source file.
            files (list[dict[str, str]]): The list of translated file download links.
        """
        file_download_blocks = []
        for idx, translated_file in enumerate(files):
            file_download_blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{translated_file['tl']}*"},
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Download",
                            "emoji": False,
                        },
                        "style": "primary",
                        "action_id": f"link_{idx}",
                        "url": translated_file["download_url"],
                    },
                }
            )
        super().__init__(
            f"Some of your files are translated and ready to be downloaded ({job_id})",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Some of your files are translated and ready to be downloaded (*{job_id}*)",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*File:*\n{source_file}"},
                        {
                            "type": "mrkdwn",
                            "text": f"*Source Language:*\n{source_lang}",
                        },
                    ],
                },
                *file_download_blocks,
            ],
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
                        {"type": "mrkdwn", "text": "`/ray [job reference]`"},
                        {"type": "mrkdwn", "text": "Your daily summary"},
                        {"type": "mrkdwn", "text": "`/ray my jobs`"},
                        {
                            "type": "mrkdwn",
                            "text": "Upload files to translate and submit a quote request",
                        },
                        {"type": "mrkdwn", "text": "`/ray new`"},
                        {"type": "mrkdwn", "text": "View your DeltaRAY connection"},
                        {"type": "mrkdwn", "text": "`/ray info`"},
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


class ConnectionInfoMessage(SlackMessage):
    """The current Slack-DeltaRAY connection details."""

    def __init__(
        self,
        ray_connection: RayConnection | None,
        user_id: str,
        team_id: str,
        app_id: str,
        channel_id: str,
    ) -> None:
        # First get Slack workspace - super group info.
        if ray_connection is not None:
            text = f"This workspace is connected to: {ray_connection.super_group.name}."
            workspace_block = {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"This workspace is connected to: *{ray_connection.super_group.name}*.",
                },
            }
        else:
            text = "This workspace is not connected to an organisation yet."
            workspace_block = {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            }
        # Next get Slack user - DeltaRAY account info.
        account_blocks = []
        if ray_connection is not None and ray_connection.client is not None:
            text = f"Your connected DeltaRAY account is: <{domains.deltaray}|{ray_connection.client.username}>"
            account_blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": text},
                }
            )
        else:
            account_blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Click this button to connect your DeltaRAY account.",
                    },
                }
            )
            account_blocks.append(
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
                }
            )
        super().__init__(
            text,
            [workspace_block, *account_blocks],
        )


class InvalidCommandMessage(TextMessage):
    """Invalid /ray command."""

    def __init__(self) -> None:
        super().__init__(
            ":no_entry_sign: Invalid command. Type `/ray help` for a list of valid commands."
        )


class ClientApprovedMessage(TextMessage):
    """A group admin approved a new client in Slack."""

    def __init__(self, approved_client: str) -> None:
        super().__init__(
            f"The user {approved_client} has been approved to join your group(s)."
        )


class ClientAlreadyApprovedMessage(TextMessage):
    """A group admin approved a new client in Slack, but the client was already
    approved.
    """

    def __init__(self, approved_client: str) -> None:
        super().__init__(f"The user {approved_client} has already been approved.")


# -----------------------------------------------------------------------------
# Ray event messages
# -----------------------------------------------------------------------------


class ClientSignupEventMessage(SlackMessage):
    def __init__(self, event: ClientSignupEvent) -> None:
        self.event = event
        super().__init__(
            "Thank you for signing up to DeltaRAY :tada:",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Thank you for signing up to DeltaRAY <{domains.deltaray}|{event.username}> :tada:",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "A notification has been sent to your Admins who will approve your account. You will be notified again once this has been approved.",
                    },
                },
            ],
        )


class ClientSignupEventAdminMessage(SlackMessage):
    def __init__(self, event: ClientSignupEvent, groups: list[ClientGroup]) -> None:
        self.event = event
        super().__init__(
            f"A new user has signed up for a DeltaRAY account: {event.first_name} {event.last_name} ({event.email})",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"A new user has signed up for a DeltaRAY account:\n{event.first_name} {event.last_name} ({event.email})",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Before this user can use the Straker Slack app, they require approval for the groups they should be associated with:",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Group(s)*:\n"
                        + "\n".join(group.label for group in groups),
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "To approve this user please click the approve button below, or alternatively if you need to change anything, please log into DeltaRAY to edit their permissions.",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "emoji": True,
                                "text": "Approve",
                            },
                            "style": "primary",
                            "action_id": "approve_pending_client",
                            "value": json.dumps(
                                {"id": event.client_id, "username": event.username}
                            ),
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "emoji": True,
                                "text": "Log into DeltaRAY",
                            },
                            "url": domains.deltaray,
                            "action_id": "link",
                        },
                    ],
                },
            ],
        )


class ClientApprovedEventMessage(SlackMessage):
    def __init__(self, groups: list[str]) -> None:
        groups_text = "\n".join(f"- *{group}*" for group in groups)
        super().__init__(
            ":raised_hands: Your DeltaRAY groups have been approved by an Admin.",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":raised_hands: Your DeltaRAY groups have been approved by an Admin:\n\n{groups_text}",
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":white_check_mark: You can now access all the features within the Straker app.",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Use `/ray help` to show some ideas of what you can do.",
                    },
                },
            ],
        )


class JobStatusChangedEventMessage(SlackMessage):
    def __init__(self, client_id: str, job_uuid: str, job_id: str, status: str) -> None:
        status_formatted = format_job_status(status)
        super().__init__(
            f"Your translation job {job_id} has changed status to: {status_formatted}",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Your translation job *{job_id}* has changed status to: {status_formatted}",
                    },
                },
                job_deltaray_link_block(job_uuid, client_id),
            ],
        )


class JobCompletedEventMessage(SlackMessage):
    def __init__(self, client_id: str, job_uuid: str, job_id: str) -> None:
        super().__init__(
            f":tada: Your translation job {job_id} is completed!",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":tada: Your translation job *{job_id}* is completed!",
                    },
                },
                job_deltaray_link_block(job_uuid, client_id),
            ],
        )


class JobCancelledEventMessage(SlackMessage):
    def __init__(self, client_id: str, job_uuid: str, job_id: str) -> None:
        super().__init__(
            f"Your translation job {job_id} has been cancelled",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Your translation job *{job_id}* has been cancelled",
                    },
                },
                job_deltaray_link_block(job_uuid, client_id),
            ],
        )


class JobQuotedEventMessage(SlackMessage):
    def __init__(self, event: JobQuoteCreatedEvent) -> None:
        currency = format_currency_symbol(event.quote.currency)
        quote_formatted = format_currency(event.quote.quote, event.quote.currency)
        turnaround_time = (
            f"within {event.turnaround_days} days" if event.turnaround_days > 0 else ""
        )
        job_url = get_job_url(event.uuid, event.client_id)
        # Show "incl. tax" next to the total cost if > the sum of the individual language prices.
        incl_tax = event.quote.quote != event.quote.quote_nett
        # Show prices for individual languages (if they exist).
        lang_price_blocks = []
        if event.quote.tl:
            lang_price_blocks = [
                {
                    "type": "section",
                    "fields": [],
                },
                {"type": "divider"},
            ]
            for lang in event.tl:
                lang_price = (
                    event.quote.tl[lang.code].price
                    if lang.code in event.quote.tl
                    else 0.0
                )
                lang_price_formatted = format_currency(lang_price, event.quote.currency)
                lang_price_blocks[0]["fields"].append(
                    {
                        "type": "mrkdwn",
                        "text": f"*{lang.label}:*\n{lang_price_formatted}",
                    }
                )

        super().__init__(
            f"Your quote is now ready :raised_hands: Straker Job Reference {event.id}",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Your quote is now ready :raised_hands:\n*<{job_url}|Straker Job Reference {event.id}>*",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Source Language:*\n{event.sl.label}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Turnaround Time:*\n{turnaround_time}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Service:*\n{event.service}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Client Reference:*\n{event.client_reference}",
                        },
                    ],
                },
                {"type": "divider"},
                *lang_price_blocks,
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Total Cost ({currency})*: {quote_formatted} {'(incl. tax)' if incl_tax else ''}",
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
                            "url": event.quote.quote_accept_url,
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
                            "url": event.quote.quote_cancel_url,
                            "action_id": "link_1",
                            "confirm": {
                                "title": {
                                    "type": "plain_text",
                                    "text": "Cancel Quote",
                                },
                                "text": {
                                    "type": "plain_text",
                                    "text": "Are you sure you want to cancel this quote?\n\n"
                                    "This action requires you to be logged in to DeltaRAY.",
                                },
                                "confirm": {"type": "plain_text", "text": "Yes"},
                                "deny": {
                                    "type": "plain_text",
                                    "text": "No",
                                },
                            },
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "More Information",
                                "emoji": True,
                            },
                            "url": job_url,
                            "action_id": "link_2",
                        },
                    ],
                },
            ],
        )
