"""Slack Messages templates."""

from urllib.parse import urlencode
from .models import SlackMessage, TextMessage
from ...config import straker_config
from ..auth import get_slack_deltaray_integration_url


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
                        "text": f":white_check_mark: Login was successful! <@{user_id}> is now connected with <{straker_config.deltaray_domain}|{ray_username}>.",
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
    def __init__(self, job_id: str, job: dict, client_id: str) -> None:
        super().__init__(
            f"Job status ({job_id}): {job['status']}",
            [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Job status ({job_id}):*"},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": "Status"},
                        {"type": "mrkdwn", "text": job["status"]},
                        {"type": "mrkdwn", "text": "Source language"},
                        {"type": "mrkdwn", "text": job["sl"]},
                        {"type": "mrkdwn", "text": "Target language(s)"},
                        {"type": "mrkdwn", "text": job["tl"]},
                        {"type": "mrkdwn", "text": "Target date"},
                        {"type": "mrkdwn", "text": job["target_date"]},
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
                            "url": f"{straker_config.deltaray_domain}/job/detail?{urlencode({'j': job['obj_uuid'], 'member_id': client_id})}",
                            "action_id": "link",
                        }
                    ],
                },
            ],
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
            f"Your connected DeltaRay account is: <{straker_config.deltaray_domain}|{username}>"
        )


class InvalidCommandMessage(TextMessage):
    def __init__(self) -> None:
        super().__init__(
            ":no_entry_sign: Invalid command. Type `/ray help` for a list of valid commands."
        )
