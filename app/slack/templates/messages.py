"""Slack Messages templates."""
# Ignore line too long lint errors
# flake8: noqa

import json
from urllib.parse import urlencode
from ray_sdk.api.v3.models import Job
from .models import SlackMessage, TextMessage, NewJobForm
from ...config import config
from ...auth.connector import get_slack_deltaray_integration_url


class OnboardingMessage(SlackMessage):
    """Message to send to onboard a new user."""

    def __init__(
        self, user_id: str, team_id: str, app_id: str, channel_id: str
    ) -> None:
        super().__init__(
            "The Straker RAY App has been sucessfully installed in your Slack workspace! :tada:",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "The Straker RAY App has been sucessfully installed in your Slack workspace! :tada:",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Sign in to your DeltaRay account to use slash commands and receive notifications about your translation jobs.",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "Connect DeltaRay account",
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
    """Message to send to prompt the user to connect their DeltaRay account."""

    def __init__(
        self, user_id: str, team_id: str, app_id: str, channel_id: str
    ) -> None:
        super().__init__(
            "Connect your DeltaRay account",
            [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "Connect your DeltaRay account"},
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "Connect DeltaRay account",
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
    """Message to send after a user successfully connects their DeltaRay
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
                        {"type": "mrkdwn", "text": "Create a new job"},
                        {"type": "mrkdwn", "text": "`/ray new`"},
                        {
                            "type": "mrkdwn",
                            "text": ":bell: Configure job notifications",
                        },
                        {"type": "mrkdwn", "text": "`/ray notifications`"},
                        {
                            "type": "mrkdwn",
                            "text": ":information_source: Show a help message",
                        },
                        {"type": "mrkdwn", "text": "`/ray help`"},
                    ],
                },
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": "*More*"}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": ":open_file_folder: Straker Help Site",
                                "emoji": True,
                            },
                            "url": "https://help.strakertranslations.com/hc/en-us",
                            "action_id": "link",
                        }
                    ],
                },
            ],
        )


class JobStatusMessage(SlackMessage):
    """Message showing the status of a translation job."""

    def __init__(self, job: Job, client_id: str) -> None:
        super().__init__(
            f"Job status ({job.id}): {job.status}",
            [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"Job status ({job.id}):"},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": "Status"},
                        {"type": "mrkdwn", "text": job.status},
                        {"type": "mrkdwn", "text": "Source language"},
                        {"type": "mrkdwn", "text": job.sl},
                        {"type": "mrkdwn", "text": "Target language(s)"},
                        {"type": "mrkdwn", "text": job.tl},
                        {"type": "mrkdwn", "text": "Target date"},
                        {
                            "type": "mrkdwn",
                            "text": job.target_date.strftime("%Y-%m-%d %H:%M:%S"),
                        },
                    ],
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "View this job in DeltaRay",
                                "emoji": True,
                            },
                            "url": f"{config.deltaray_domain}/job/detail?{urlencode({'j': job.uuid, 'member_id': client_id})}",
                            "action_id": "link",
                        }
                    ],
                },
            ],
        )


class InvalidJobMessage(TextMessage):
    """The user does not have access to the job."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"Cannot find the job: `{job_id.upper()}`")


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
            "Hi there :wave: here are some ideas of what you can do:",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Hi there :wave: here are some ideas of what you can do:",
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": "Check your job status"},
                        {"type": "mrkdwn", "text": "`/ray [TJ number]`"},
                        {"type": "mrkdwn", "text": "Create a new job"},
                        {"type": "mrkdwn", "text": "`/ray new`"},
                        {
                            "type": "mrkdwn",
                            "text": ":bell: Configure job notifications",
                        },
                        {"type": "mrkdwn", "text": "`/ray notifications`"},
                        {
                            "type": "mrkdwn",
                            "text": "Show your connected DeltaRay account",
                        },
                        {"type": "mrkdwn", "text": "`/ray whoami`"},
                    ],
                },
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": "*More*"}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": ":open_file_folder: Straker Help Site",
                                "emoji": True,
                            },
                            "url": "https://help.strakertranslations.com/hc/en-us",
                            "action_id": "link",
                        }
                    ],
                },
            ],
        )


class WhoamiMessage(TextMessage):
    """Message showing which DeltaRay account is currently connected."""

    def __init__(self, username: str) -> None:
        super().__init__(
            f"Your connected DeltaRay account is: <{config.deltaray_domain}|{username}>"
        )


class InvalidCommandMessage(TextMessage):
    """Invalid /ray command."""

    def __init__(self) -> None:
        super().__init__(
            ":no_entry_sign: Invalid command. Type `/ray help` for a list of valid commands."
        )
