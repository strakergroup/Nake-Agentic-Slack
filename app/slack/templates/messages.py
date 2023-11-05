"""Slack Messages templates."""
# Ignore line too long lint errors
# flake8: noqa

from typing import Any
import json
from ray_sdk.api.v3.models import Job, Pagination, Quote

from .models import NewJobForm
from .blocks import job_link_block, quote_message_block, job_prediction_block
from ...ray.events.models import (
    ClientSignupEvent,
    JobQuoteCreatedEvent,
    ClientGroup,
    JobQuoteAcceptedEvent,
)
from ...ray.utils import (
    format_predictions,
    get_job_url,
    format_job_status,
    format_datetime_slack,
    format_job_due_date_slack,
    format_job_prediction,
)
from ...config import config, domains, Environment
from ...auth.connector import (
    RayClient,
    RayConnection,
    get_language_cloud_connect_url,
)
from slack_bolt.context.async_context import AsyncBoltContext


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
        self, user_id: str, team_id: str, enterprise_id: str | None, channel_id: str
    ) -> None:
        super().__init__(
            "Welcome to RAY Translate for Slack! :tada:",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Welcome to RAY Translate for Slack! :tada:",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Connect your LanguageCloud account to get details about your translation jobs.",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "Connect LanguageCloud account",
                            },
                            "style": "primary",
                            "url": get_language_cloud_connect_url(
                                user_id, team_id, enterprise_id, channel_id
                            ),
                            "action_id": "login",
                        }
                    ],
                },
            ],
        )


class LoginMessage(SlackMessage):
    """Message to send to prompt the user to connect their LanguageCloud account."""

    GET_JOB = "get_job"
    NEW_JOB = "new_job"
    INSIGHTS = "insights"

    def __init__(
        self,
        user_id: str,
        team_id: str,
        enterprise_id: str | None,
        channel_id: str,
        ray_client: RayClient | None = None,
        variation: str | None = None,
    ) -> None:
        """Constructor for the login Slack message. If the Slack user already has
        a connected LanguageCloud account, creates a variation with the client username
        in the message.

        Args:
            user_id (str): The Slack user ID.
            team_id (str): The Slack team ID.
            enterprise_id (str): The Slack enterprise ID.
            channel_id (str): The Slack channel ID to send the successful login message to.
            ray_client (RayClient | None, optional): Pass the RayClient info to use a
                variation of the message. Defaults to None.
            variation (str | None, optional): The variation of the message to use.
                The options are in the class variables. Defaults to None.
        """
        self._user_id = user_id
        self._team_id = team_id
        self._enterprise_id = enterprise_id
        self._channel_id = channel_id
        self._ray_client = ray_client
        self._variation = variation

        # Have variations of the login message depending on the arguments.
        block_text = "Click this button to connect your LanguageCloud account."
        if variation == self.GET_JOB:
            block_text = "Connect your LanguageCloud account to view your jobs."
        elif variation == self.NEW_JOB:
            block_text = (
                "Connect your LanguageCloud account to submit a new translation job."
            )
        elif variation == self.INSIGHTS:
            block_text = "Connect your LanguageCloud account to view your insights."
        elif isinstance(ray_client, RayClient):
            block_text = (
                f"Your connected LanguageCloud account is: <{domains.languagecloud}|{ray_client.username}>.\n"
                "You can connect a different account by clicking this button."
            )

        super().__init__(
            "Connect your LanguageCloud account",
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
                                "text": "Connect LanguageCloud account",
                            },
                            "style": "primary",
                            "url": get_language_cloud_connect_url(
                                user_id, team_id, enterprise_id, channel_id
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
            enterprise_id=self._enterprise_id,
            channel_id=self._channel_id,
            ray_client=self._ray_client,
            variation=variation,
        )


class WelcomeBackMessage(SlackMessage):
    """Message to send after a user successfully connects their LanguageCloud
    account.
    """

    def __init__(self, user_id: str) -> None:
        super().__init__(
            ":white_check_mark: Welcome back",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "plain_text",
                        "emoji": True,
                        "text": "Welcome :wave: \n\nChoose an option below to get started."
                    }
                },
                {
                    "type": "divider"
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🔍 Search allows you to search for a specific Translation Job (TJ). "
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "Search"
                        },
                        "action_id": "job_search"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🚦 Jobs provides an update on the status of recently submitted jobs."
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "Jobs"
                        },
                        "action_id": "all_summary"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🗂️ Quote opens the form to upload documents for translation."
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Quote"
                        },
                        "action_id": "new_job"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "📊 Insights uses AI to gather and show data about your translation experience"
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "Insights"
                        },
                        "action_id": "report_insights"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*<https://help.strakertranslations.com/hc/en-us/articles/22925760887833-Slack-app-functions|Show more options>*"
                    }
                },
                {
                    "type": "divider"
                },
                {
                    "type": "section",
                    "block_id": "sectionBlockOnlyMrkdwn",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Instead of buttons try using natural language, ask questions like, *What's the status of TJXZ12345?* or *Show me jobs completed in the last 4 hours.*"
                    }
                }
            ],
        )


class SuccessfulLoginMessage(SlackMessage):
    """Message to send after a user successfully connects their LanguageCloud
    account.
    """

    def __init__(self, user_id: str, ray_username: str) -> None:
        super().__init__(
            ":white_check_mark: Login was successful!",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "plain_text",
                        "emoji": True,
                        "text": "Welcome :wave: \n\nChoose an option below to get started."
                    }
                },
                {
                    "type": "divider"
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🔍 Search allows you to search for a specific Translation Job (TJ). "
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "Search"
                        },
                        "action_id": "job_search"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🚦 Jobs provides an update on the status of recently submitted jobs."
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "Jobs"
                        },
                        "action_id": "all_summary"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🗂️ Quote opens the form to upload documents for translation."
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Quote"
                        },
                        "action_id": "new_job"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "📊 Insights uses AI to gather and show data about your translation experience"
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "Insights"
                        },
                        "action_id": "report_insights"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*<https://help.strakertranslations.com/hc/en-us/articles/22925760887833-Slack-app-functions|Show more options>*"
                    }
                },
                {
                    "type": "divider"
                },
                {
                    "type": "section",
                    "block_id": "sectionBlockOnlyMrkdwn",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Instead of buttons try using natural language, ask questions like, *What's the status of TJXZ12345?* or *Show me jobs completed in the last 4 hours.*"
                    }
                }
            ],
        )


class LogoutMessage(SlackMessage):
    """Message with a button disconnect a user's LanguageCloud account."""

    def __init__(self, ray_username: str) -> None:
        super().__init__(
            "Disconnect your LanguageCloud account",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Click this button to disconnect your LanguageCloud account: <{domains.languagecloud}|{ray_username}>.",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "Disconnect LanguageCloud account",
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
    """A Slack user's LanguageCloud account was successfully disconnected."""

    def __init__(self, user_id: str, ray_username: str | None = None) -> None:
        block_message = (
            f"Your LanguageCloud account <{domains.languagecloud}|{ray_username}> is now disconnected from <@{user_id}>."
            if ray_username
            else f"Your LanguageCloud account is now disconnected from <@{user_id}>."
        )
        super().__init__(
            "Your LanguageCloud account is now disconnected.",
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
                        "text": "You can use `/ray connect` to connect your LanguageCloud account again.",
                    },
                },
            ],
        )


class JobStatusMessage(SlackMessage):
    """Message showing the status of a translation job."""

    def __init__(self, job: Job, client_id: str, job_prediction: str = "") -> None:
        job_status_block = [
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
            job_link_block(job.uuid, client_id),
        ]
        if job.status != "COMPLETED" and job.batches != "[]":
            job_status_block.insert(
                3,
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "Show In Progress Files",
                                "emoji": True,
                            },
                            "action_id": "batch_list_1",
                            "value": json.dumps(
                                {
                                    "id": job.id,
                                    "page": 1,
                                    "page_size": 5,
                                    "replace_original": False,
                                }
                            ),
                        }
                    ],
                },
            )
        if job.status == "COMPLETED":
            job_status_block.insert(
                3,
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "Show Completed Files",
                                "emoji": True,
                            },
                            "action_id": "file_list_1",
                            "value": json.dumps(
                                {
                                    "id": job.id,
                                    "page": 1,
                                    "page_size": 5,
                                    "replace_original": False,
                                }
                            ),
                        }
                    ],
                },
            )
        if job_prediction != "":
            job_status_block.insert(
                1,
                job_prediction_block(
                    format_job_prediction(job_prediction, job.target_date)
                ),
            )
        super().__init__(
            f"Job status ({job.id}): {format_job_status(job.status)}",
            job_status_block,
        )


class JobDetailsMessage(SlackMessage):
    """Message showing the details of a translation job."""

    def __init__(self, job: Job, client_id: str, job_prediction: str = "") -> None:
        job_detail_block = [
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
                        "text": f"*Project Manager*\n{job.project_manager.first_name} {job.project_manager.last_name}",
                    },
                ],
            },
            job_link_block(job.uuid, client_id),
        ]
        if job.status != "COMPLETED" and job.batches != "[]":
            job_detail_block.insert(
                3,
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "Show In Progress Files",
                                "emoji": True,
                            },
                            "action_id": "batch_list_1",
                            "value": json.dumps(
                                {
                                    "id": job.id,
                                    "page": 1,
                                    "page_size": 5,
                                    "replace_original": False,
                                }
                            ),
                        }
                    ],
                },
            )
        if job.status == "COMPLETED":
            job_detail_block.insert(
                3,
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "Show Completed Files",
                                "emoji": True,
                            },
                            "action_id": "file_list_1",
                            "value": json.dumps(
                                {
                                    "id": job.id,
                                    "page": 1,
                                    "page_size": 5,
                                    "replace_original": False,
                                }
                            ),
                        }
                    ],
                },
            )
        if job_prediction != "":
            job_detail_block.insert(
                1,
                job_prediction_block(
                    format_job_prediction(job_prediction, job.target_date)
                ),
            )
        super().__init__(
            f"The information for {job.id} is below:",
            job_detail_block,
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
        in_progress_count_24: int,
        in_progress_due: int,
        completed: int,
        validation: int,
        pending_quotes: int,
        order_now: int,
        predictions: dict,
        all_jobs: bool = False,
    ) -> None:
        """The constructor.

        Args:
            in_progress (int): The total number of jobs in progress.
            completed (int): The number of jobs completed in the past 7 days.
            validation (int): The total number of jobs in validation.
            pending_quotes (int): The total number of pending quotes.
            order_now (int): The total number of jobs ready to order.
        """
        sections = []
        if in_progress > 0 or all_jobs:
            sections.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*In Progress Jobs*\n*     {in_progress} Total Job(s)*",
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "View More",
                        },
                        "action_id": "job_list",
                        "value": "IN_PROGRESS",
                    },
                }
            )
            if in_progress_count_24 > 0:
                sections.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*     {in_progress_count_24} job(s)* accepted in the last 24 hours",
                        },
                        "accessory": {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "emoji": True,
                                "text": "View More",
                            },
                            "action_id": "job_list",
                            "value": "IN_PROGRESS:ACCEPTED:24H",
                        },
                    },
                )
            if in_progress_due > 0:
                sections.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*     {in_progress_due} job(s)* due within 24 hours",
                        },
                        "accessory": {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "emoji": True,
                                "text": "View More",
                            },
                            "action_id": "job_list",
                            "value": "IN_PROGRESS:DUE:24H",
                        },
                    },
                )

            if config.environment != Environment.production:
                if (predictions["on_time"]) > 0:
                    sections.append(
                        job_prediction_block(
                            f"*     :large_green_circle: {predictions['on_time']} {'job is' if int(predictions['on_time']) == 1 else 'jobs are'}* predicted to be on-time"
                        )
                    ),
                if (predictions["late"]) > 0 or (predictions["over_due"]) > 0:
                    sections.append(
                        job_prediction_block(
                            f"*     :large_orange_circle: {int(predictions['late']) + int(predictions['over_due'])} {'job' if int(predictions['late']) + int(predictions['over_due']) == 1 else 'jobs'}* may be behind schedule"
                        )
                    )
        if completed > 0 or all_jobs:
            sections.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Completed Jobs*\n*     {completed} job(s)* completed in the past 7 days",
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "View More",
                        },
                        "action_id": "job_list",
                        "value": "COMPLETED:7D",
                    },
                },
            )
        if validation > 0 or all_jobs:
            sections.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Validation*\n*     {validation} job(s)* currently being validated",
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "View More",
                        },
                        "action_id": "job_list",
                        "value": "VALIDATION",
                    },
                },
            )
        if pending_quotes > 0 or all_jobs:
            sections.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Pending Quotes*\n*     {pending_quotes} quote(s)* pending",
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "View More",
                        },
                        "action_id": "job_list",
                        "value": "PENDING_QUOTES",
                    },
                }
            )
        if order_now > 0 or all_jobs:
            sections.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Order Now*\n*     {order_now} job(s)* to order",
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "View More",
                        },
                        "action_id": "job_list",
                        "value": "ORDER_NOW",
                    },
                },
            )
        if len(sections) == 0:
            sections.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "No jobs found.",
                    },
                },
            )
        if not all_jobs and (
            not in_progress
            or not in_progress_count_24
            or not in_progress_due
            or not completed
            or not validation
            or not pending_quotes
            or not order_now
        ):
            sections.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f" ",
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "View All",
                        },
                        "action_id": "all_summary",
                    },
                },
            )
        super().__init__(
            f"In Progress Jobs: {in_progress} jobs currently in progress...",
            sections,
        )


class JobListMessage(SlackMessage):
    """A list of the client's jobs."""

    def __init__(
        self,
        preset: str,
        title: str,
        jobs: list[Job],
        pagination: Pagination,
        job_predictions: list[dict],
        client_ref: str = "",
    ) -> None:
        jobs_blocks = []

        # If there is any jobs result
        if jobs:
            for i, job in enumerate(jobs):
                job_text = f"*{job.id}*"
                if job.reference:
                    job_text += f"\nRef: {job.reference}"

                job_text += f"\n{job.sl.shortname.upper()} > {', '.join(lang.shortname.upper() for lang in job.tl)}"
                job_text += "\nDue: " + format_job_due_date_slack(
                    job.target_date, job.status, traffic_light=True
                )
                prediction = (
                    next(
                        prediction.get("prediction", "")
                        for prediction in job_predictions
                        if prediction["job_id"] == job.id.upper()
                    )
                    if job_predictions
                    else ""
                )
                formatted_job_prediction = (
                    format_job_prediction(prediction, job.target_date)
                    if prediction != ""
                    else ""
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
                            "value": json.dumps({"id": job.id, "status": job.status}),
                        },
                    }
                )
                if job.status != "COMPLETED" and job.batches != "[]":
                    jobs_blocks.append(
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Show In Progress Files",
                                        "emoji": True,
                                    },
                                    "action_id": "batch_list_1",
                                    "value": json.dumps(
                                        {
                                            "id": job.id,
                                            "page": 1,
                                            "page_size": 5,
                                            "replace_original": False,
                                        }
                                    ),
                                }
                            ],
                        },
                    )
                if formatted_job_prediction != "":
                    jobs_blocks.append(job_prediction_block(formatted_job_prediction))
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


class InsightsMessage(SlackMessage):
    def __init__(self, message: str):
        super().__init__(
            f":idea: Here are your insights",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":idea: *Here are your insights*",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": ">" + message},
                    ],
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


# TODO update this for quote command?
class HelpMessage(SlackMessage):
    """Help message showing how to use the app."""

    def __init__(self, context: AsyncBoltContext) -> None:
        super().__init__(
            "Hi there :wave: here are some ideas of what you can currently do with our app:",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Hi there:wave: \n\nHow can we help?",
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🔍 Status allows you to search for a specific job. "
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Status"
                        },
                        "action_id" : "job_search"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🚦 Jobs provides an update on the status of recently submitted jobs."
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Jobs"
                        },
                        "action_id" : "all_summary"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🗂️ Quote opens the form to upload documents for translation."
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Quote"
                        },
                        "action_id" : "quote"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "📊 Insights uses AI to gather and show data about your translation experience"
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Insights"
                        },
                        "action_id": "report_insights"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":globe_with_meridians: View your LanguageCloud connection."
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Info"
                        },
                        "action_id": "account_info"
                    }
                },
                {
                    "type": "section",
                    "block_id": "sectionBlockWithButton",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":Seedling: Connect your LanguageCloud account"
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Connect",
                        },
                        "url": get_language_cloud_connect_url(
                            context["user_id"],
                            context["team_id"],
                            context.get("enterprise_id"),
                            context["channel_id"],
                        ),
                    }
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


class QuoteMessage(SlackMessage):
    """Quote message button to pop up job form."""

    def __init__(self) -> None:
        super().__init__(
            "New Quote Message",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Please upload your files to translate in the message composer below, or alternatively, if you have already uploaded your files, click the *Submit a Quote* button below",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Submit a Quote"},
                            "style": "primary",
                            "action_id": "new_job",
                        }
                    ],
                },
            ],
        )


class WhatsNextMessage(SlackMessage):
    def __init__(self) -> None:
        url = "https://help.strakertranslations.com/hc/en-us/articles/10021384538393-Current-Upcoming-Features"
        super().__init__(
            "Click here to see the upcoming features of our app",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"<{url}|Click here> to see the upcoming features of our app.",
                    },
                },
            ],
        )


class ConnectionInfoMessage(SlackMessage):
    """The current Slack - LanguageCloud connection details."""

    def __init__(
        self,
        ray_connection: RayConnection | None,
        user_id: str,
        team_id: str,
        enterprise_id: str | None,
        channel_id: str,
    ) -> None:
        # First get Slack workspace - super group info.
        if ray_connection is not None:
            super_group_names = [group.name for group in ray_connection.super_group]
            super_group_names_str = ", ".join(super_group_names)
            text = f"Your Slack workspace is connected with: {super_group_names_str}."
            workspace_block = {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Your Slack workspace is connected with: *{super_group_names_str}*.",
                },
            }
        else:
            text = "Your Slack workspace is not connected with an organisation yet."
            workspace_block = {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            }
        # Next get Slack user - LanguageCloud account info.
        account_blocks = []
        if ray_connection is not None and ray_connection.client is not None:
            text = f"Your connected LanguageCloud account is: <{domains.languagecloud}|{ray_connection.client.username}>"
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
                        "text": "Click this button to connect your LanguageCloud account.",
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
                                "text": "Connect LanguageCloud account",
                            },
                            "style": "primary",
                            "url": get_language_cloud_connect_url(
                                user_id, team_id, enterprise_id, channel_id
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


class JobQuotedMessage(SlackMessage):
    def __init__(self, quote: Quote) -> None:
        job_url = get_job_url(quote.uuid, quote.client_id)
        super().__init__(
            f"Pending Quote: Straker Job Reference {quote.id}",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*<{job_url}|Straker Job Reference {quote.id}>*",
                    },
                },
            ]
            + quote_message_block(quote, job_url),
        )


# -----------------------------------------------------------------------------
# Ray event messages
# -----------------------------------------------------------------------------


class ClientSignupEventMessage(SlackMessage):
    def __init__(self, event: ClientSignupEvent) -> None:
        self.event = event
        super().__init__(
            "Thank you for signing up to LanguageCloud :tada:",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Thank you for signing up to LanguageCloud <{domains.languagecloud}|{event.username}> :tada:",
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
            f"A new user has signed up for a LanguageCloud account: {event.first_name} {event.last_name} ({event.email})",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"A new user has signed up for a LanguageCloud account:\n{event.first_name} {event.last_name} ({event.email})",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Before this user can use RAY Translate for Slack, they require approval for the groups they should be associated with:",
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
                        "text": "To approve this user please click the approve button below, or alternatively if you need to change anything, please log into LanguageCloud to edit their permissions.",
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
                                "text": "Log into LanguageCloud",
                            },
                            "url": domains.languagecloud,
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
            ":raised_hands: Your LanguageCloud groups have been approved by an Admin.",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":raised_hands: Your LanguageCloud groups have been approved by an Admin:\n\n{groups_text}",
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":white_check_mark: You can now access all the features within RAY Translate.",
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
                job_link_block(job_uuid, client_id),
            ],
        )


class JobCompletedEventMessage(SlackMessage):
    def __init__(
        self, client_id: str, job_uuid: str, job_id: str, target_languages: list[str]
    ) -> None:
        """Notification sent to the client when a translation job is completed.

        Args:
            client_id (str): The job's client ID
            job_uuid (str): The job UUID.
            job_id (str): The job ID (TJ number).
            target_languages (list[str]): The job's target languages (formatted name).
        """
        if len(target_languages) > 3:
            target_lang_text = ", ".join(target_languages[:2]) + ", and more"
        else:
            target_lang_text = ", ".join(target_languages)
        super().__init__(
            f"Your files for {job_id} are ready to download :white_check_mark:",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Your files for *{job_id}* in *{target_lang_text}* are ready to download :white_check_mark:",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "Show Completed Files",
                                "emoji": True,
                            },
                            "action_id": "file_list_1",
                            "value": json.dumps(
                                {
                                    "id": job_id,
                                    "page": 1,
                                    "page_size": 5,
                                    "replace_original": False,
                                }
                            ),
                        }
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Please log into LanguageCloud below to access your completed files.",
                    },
                },
                job_link_block(job_uuid, client_id),
            ],
        )


class JobCancelledEventMessage(SlackMessage):
    def __init__(self, client_id: str, job_uuid: str, job_id: str) -> None:
        job_url = get_job_url(job_uuid, client_id)
        super().__init__(
            f"Your translation job {job_id} has been cancelled",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Your translation job *<{job_url}|{job_id}>* has been cancelled.",
                    },
                },
            ],
        )


class JobQuoteAcceptedEventMessage(SlackMessage):
    def __init__(self, event: JobQuoteAcceptedEvent) -> None:
        target_date = event.target_date
        id = event.id
        super().__init__(
            f"Quote Accepted for {id}. Your job will be completed before {format_datetime_slack(target_date)}.",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":clap: Quote Accepted for *{id}*. Your job will be completed before {format_datetime_slack(target_date)}.",
                    },
                },
                job_link_block(event.uuid, event.client_id),
            ],
        )


class JobQuoteCancelledEventMessage(SlackMessage):
    def __init__(self, client_id: str, job_uuid: str, job_id: str) -> None:
        job_url = get_job_url(job_uuid, client_id)
        super().__init__(
            f"We have cancelled the quote for {job_id}.",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"We have cancelled the quote for *<{job_url}|{job_id}>*.",
                    },
                },
            ],
        )


class JobQuotedEventMessage(SlackMessage):
    def __init__(self, event: JobQuoteCreatedEvent) -> None:
        job_url = get_job_url(event.uuid, event.client_id)
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
            ]
            + quote_message_block(event, job_url),
        )


class JobDelayMessage(SlackMessage):
    def __init__(self) -> None:
        message = "Our LanguageCloud on-time AI prediction model has indicated that your job may be tracking behind schedule.\n\n"
        message += "Our Project Managers have been notified and will be taking action to ensure that we still meet your due date. "
        message += "If there is going to be a delay meeting your due dates, our Project Managers or your Account Manager will inform you. "
        message += "This is only a prediction and should not be taken as an indication that your job is going to be late.\n\n"
        message += "This status is updated in real time so can change if we predict it is tracking on time again."

        super().__init__(
            message,
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": message,
                    },
                },
            ],
        )


class BatchListMessage(SlackMessage):
    """Message showing the list of in progress files."""

    def __init__(self, job: Job, client_id: str) -> None:
        title = f"The in progress file list for *{job.id}* is below:"

        job_file_block = []
        # Prepare download links prefix
        if config.environment == Environment.production:
            download_prefix = "https://workbench.strakertranslations.com/shadomx/apps/wbadmin/fw1/index.cfm?action=download.translation&filePath="
        elif config.environment == Environment.uat:
            download_prefix = "https://uat-workbench.strakertranslations.com/shadomx/apps/wbadmin/fw1/index.cfm?action=download.translation&filePath="
        elif config.environment == Environment.local:
            download_prefix = "https://local-workbench.strakertranslations.com/shadomx/apps/wbadmin/fw1/index.cfm?action=download.translation&filePath="
        else:
            download_prefix = "https://local-workbench.strakertranslations.com/shadomx/apps/wbadmin/fw1/index.cfm?action=download.translation&filePath="

        # If there is any jobs result
        job_batches = json.loads(job.batches)
        job_text = ""

        # Loop for each sub batch inside a job and print out detailed information and download links if available
        for batch in job_batches:
            job_text += f"\n{batch['batch_label'].upper()} \n    - {batch['source_lang'].upper()} > {batch['target_lang'].upper()}"
            if job.status == "COMPLETED" and batch["generated_file"] != "":
                job_text += f"\n    - {job.status.upper()} - {batch['batch_status'].upper()} - <{download_prefix + batch['generated_file']}|DOWNLOAD>"
            elif (
                job.status != "COMPLETED"
                and batch["generated_file"] != ""
                and batch["batch_status"] in ("TRANSLATED", "REVIEWED", "QA_REVIEWED", "VALIDATED", "VALIDATED 2")
            ):
                job_text += f"\n    - {job.status.upper()} - {batch['batch_status'].upper()} - <{download_prefix + batch['generated_file']}|DOWNLOAD>"
            else:
                job_text += f"\n    - {job.status.upper()} - {batch['batch_status'].upper()}"

        job_file_block.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": job_text,
                },
            }
        )

        pagination_blocks = []
        if job.pagination.total_pages > 1:
            pagination_blocks.append({"type": "actions", "elements": []})
            if job.pagination.page > 1:
                pagination_blocks[0]["elements"].append(
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Show previous files",
                            "emoji": True,
                        },
                        "action_id": "batch_list_0",
                        "value": json.dumps(
                            {
                                "id": job.id,
                                "page": job.pagination.page - 1,
                                "page_size": job.pagination.rows_per_page,
                                "replace_original": True,
                            }
                        ),
                    }
                )
            if job.pagination.page < job.pagination.total_pages:
                pagination_blocks[0]["elements"].append(
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Show more files",
                            "emoji": True,
                        },
                        "action_id": "batch_list_1",
                        "value": json.dumps(
                            {
                                "id": job.id,
                                "page": job.pagination.page + 1,
                                "page_size": job.pagination.rows_per_page,
                                "replace_original": True,
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
                *job_file_block,
                *pagination_blocks,
            ],
        )


class FileListMessage(SlackMessage):
    """Message showing the list of translation files."""

    def __init__(self, job: Job, client_id: str) -> None:
        title = f"The completed file list for *{job.id}* is below:"
        job_file_block = []
        for x in job.translated_file:
            url = {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": x["file_name"] + " " + job.sl.name + "-" + x["lang"],
                },
                "accessory": {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "emoji": True,
                        "text": "Download",
                    },
                    "url": x["download_url"],
                    "action_id": "link",
                    "style": "primary",
                },
            }
            job_file_block.insert(2, url)

        pagination_blocks = []
        if job.pagination.total_pages > 1:
            pagination_blocks.append({"type": "actions", "elements": []})
            if job.pagination.page > 1:
                pagination_blocks[0]["elements"].append(
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Show previous files",
                            "emoji": True,
                        },
                        "action_id": "file_list_0",
                        "value": json.dumps(
                            {
                                "id": job.id,
                                "page": job.pagination.page - 1,
                                "page_size": job.pagination.rows_per_page,
                                "replace_original": True,
                            }
                        ),
                    }
                )
            if job.pagination.page < job.pagination.total_pages:
                pagination_blocks[0]["elements"].append(
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Show more files",
                            "emoji": True,
                        },
                        "action_id": "file_list_1",
                        "value": json.dumps(
                            {
                                "id": job.id,
                                "page": job.pagination.page + 1,
                                "page_size": job.pagination.rows_per_page,
                                "replace_original": True,
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
                *job_file_block,
                *pagination_blocks,
            ],
        )


class ReportInsightsMessage(SlackMessage):
    def __init__(self, plan: str) -> None:
            if plan == "Free":
                message = "The insights feature is only avaiable on the Growth and Enterprise plans."
            else:
                message = "Use can use the message pane below to type your insights request using natural language. Get turn around times, cost, or validation quality. An example:\n>Can you tell me how many jobs have been delivered on time in the last 30 days"
            super().__init__(
                f":idea: Here are your insights",
                [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": message
                        }
                    }
                ],
            )


class JobTargetsNoIdMessage(TextMessage):
    """Message to send when the user asks for a job targets but has not given
    a TJ number.
    """

    def __init__(self) -> None:
        super().__init__(
            "To check the targets of your job, type the reference number (e.g. TJ123456)."
        )


class JobTargetLangMessage(SlackMessage):
    """Message showing the list of translation files."""

    def __init__(self, job: Job, client_id: str) -> None:
        if job.status == "PENDING_QUOTES":
            title = f"*{job.id}* waiting for quotation."
        elif job.status == "ORDER_NOW":
            title = f"Job *{job.id}* waiting for order."
        else:
            title = f"Job *{job.id}* no targets information."
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
            ],
        )