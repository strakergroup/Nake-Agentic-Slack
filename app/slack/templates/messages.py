"""Slack Messages templates."""

from typing import Any
import json
from app.slack.select_options import get_auto_translate_language_options
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
    is_min_langugagecloud_plan,
)
from ...ray.settings import get_auto_translate_language_name
from ..utils import format_strings_display
from ...config import config, domains, Environment
from ...auth.connector import (
    RayClient,
    RayConnection,
    get_language_cloud_connect_url,
    encrpyt_slack_sso_token,
)
from slack_bolt.context.async_context import AsyncBoltContext
from app.translate import _


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
        self,
        user_id: str,
        team_id: str,
        enterprise_id: str | None,
        channel_id: str,
        prompt_login: bool = True,
    ) -> None:
        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _("Welcome to Straker Translate for Slack! :tada:"),
                },
            }
        ]
        if prompt_login:
            blocks.extend(
                [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": _(
                                "Connect your LanguageCloud account to get details about your translation jobs."
                            ),
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": _("Connect LanguageCloud account"),
                                },
                                "style": "primary",
                                "url": get_language_cloud_connect_url(
                                    user_id, team_id, enterprise_id, channel_id
                                ),
                                "action_id": "login",
                            }
                        ],
                    },
                ]
            )
        super().__init__(_("Welcome to Straker Translate for Slack! :tada:"), blocks)


class LoginMessage(SlackMessage):
    """Message to send to prompt the user to connect their LanguageCloud account."""

    GET_JOB = "get_job"
    NEW_JOB = "new_job"
    INSIGHTS = "insights"
    CANCEL_JOB = "cancel_job"
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
        elif variation == self.CANCEL_JOB:
            block_text = "Connect your LanguageCloud account to cancel your job."
        elif isinstance(ray_client, RayClient):
            block_text = (
                "Your connected LanguageCloud account is: <{domains.languagecloud}|{ray_client.username}>.\n"
                + "You can connect a different account by clicking this button."
            )
            if ray_client.sso:
                block_text = (
                    "Your connected LanguageCloud account is: *{ray_client.username}*."
                )
        msg = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": _(block_text)},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Connect LanguageCloud account"),
                        },
                        "style": "primary",
                        "url": get_language_cloud_connect_url(
                            user_id, team_id, enterprise_id, channel_id
                        ),
                        "action_id": "login",
                    }
                ],
            },
        ]
        if config.environment == Environment.production:
            e_id = "EUJJ37YFR"
            t_id = "T0360HUQKS9"
        else:
            e_id = "E04RDMG8XP1"
            t_id = "T02FDFCGK"
        if enterprise_id:
            if (enterprise_id == e_id) and ray_client is None:
                msg[1]["elements"].insert(
                    0,
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Direct Login"),
                        },
                        "style": "primary",
                        "action_id": "login_sso",
                    },
                )
            elif (enterprise_id == e_id) and ray_client is not None and ray_client.sso:
                msg.pop(1)
                msg.append(
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": _("Login to LanguageCloud"),
                                },
                                "style": "primary",
                                "url": encrpyt_slack_sso_token(ray_client.username),
                                "action_id": "login",
                            }
                        ],
                    },
                )
        elif team_id == t_id and ray_client is None:
            msg[1]["elements"].insert(
                0,
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": _("Direct Login"),
                    },
                    "style": "primary",
                    "action_id": "login_sso",
                },
            )
        elif team_id == t_id and ray_client is not None and ray_client.sso:
            msg.pop(1)
            msg.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Login to LanguageCloud"),
                            },
                            "style": "primary",
                            "url": encrpyt_slack_sso_token(ray_client.username),
                            "action_id": "login",
                        }
                    ],
                },
            )
        super().__init__(
            "Connect your LanguageCloud account",
            msg,
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
            "Welcome back :wave:",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "plain_text",
                        "emoji": True,
                        "text": _(
                            "Welcome :wave: \n\nChoose an option below to get started."
                        ),
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "🔍 Search allows you to search for specific Translation Jobs (TJs). "
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Search"),
                        },
                        "action_id": "job_search",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            ":vertical_traffic_light: Jobs provides an update on the status of recently submitted jobs."
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Jobs"),
                        },
                        "action_id": "all_summary",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "🗂️ Click 'New translation job' to select documents uploaded through the message box below. Note this will send the selected document off for translation."
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("New translation job"),
                        },
                        "action_id": "new_job",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _("🔴 Cancel your job"),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Cancel"),
                        },
                        "action_id": "cancel_job",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            ":bar_chart: Insights uses AI to gather and show data about your translation experience"
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Insights"),
                        },
                        "action_id": "report_insights",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(":blue_book: Learn The Basics"),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Help Centre"),
                        },
                        "url": "https://help.strakertranslations.com/hc/en-us/categories/10020714644633-Apps",
                        "action_id": "link_2",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*<https://help.strakertranslations.com/hc/en-us/articles/22925760887833-Slack-app-functions|{_('Show more options')}>*",
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "block_id": "sectionBlockOnlyMrkdwn",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "Instead of buttons try using natural language, ask questions like, *What's the status of TJXZ12345?* or *Show me jobs completed in the last 4 hours.*"
                        ),
                    },
                },
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
                        "text": _(
                            "Welcome :wave: \n\nChoose an option below to get started."
                        ),
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "🔍 Search allows you to search for specific Translation Jobs (TJs). "
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Search"),
                        },
                        "action_id": "job_search",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            ":vertical_traffic_light: Jobs provides an update on the status of recently submitted jobs."
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Jobs"),
                        },
                        "action_id": "all_summary",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "🗂️ Click 'New translation job' to select documents uploaded through the message box below.\n Note this will send the selected document off for translation."
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("New translation job"),
                        },
                        "action_id": "new_job",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _("🔴 Cancel your job"),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Cancel"),
                        },
                        "action_id": "cancel_job",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            ":bar_chart: Insights uses AI to gather and show data about your translation experience"
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Insights"),
                        },
                        "action_id": "report_insights",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(":blue_book: Learn The Basics"),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Help Centre"),
                        },
                        "url": "https://help.strakertranslations.com/hc/en-us/categories/10020714644633-Apps",
                        "action_id": "link_2",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*<https://help.strakertranslations.com/hc/en-us/articles/22925760887833-Slack-app-functions|{_('Show more options')}>*",
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "block_id": "sectionBlockOnlyMrkdwn",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "Instead of buttons try using natural language, ask questions like, *What's the status of TJXZ12345?* or *Show me jobs completed in the last 4 hours.*"
                        ),
                    },
                },
            ],
        )


class LogoutMessage(SlackMessage):
    """Message with a button disconnect a user's LanguageCloud account."""

    def __init__(self, ray_client: RayClient) -> None:
        text = _(
            "Click this button to disconnect your LanguageCloud account: <{domains.languagecloud}|{ray_client.username}>."
        )
        if ray_client.sso:
            text = _(
                "Click this button to disconnect your LanguageCloud account: *{ray_client.username}*."
            )
        super().__init__(
            "Disconnect your LanguageCloud account",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": text,
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Disconnect LanguageCloud account"),
                            },
                            "style": "danger",
                            "action_id": "disconnect",
                            "value": ray_client.username,
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Hide this message"),
                            },
                            "action_id": "delete_ephemeral_message",
                        },
                    ],
                },
            ],
        )


class SuccessfulLogoutMessage(SlackMessage):
    """A Slack user's LanguageCloud account was successfully disconnected."""

    def __init__(
        self, user_id: str, is_sso: bool = False, ray_username: str | None = None
    ) -> None:
        # TODO: Translation fix this
        text = _(
            "Your LanguageCloud account <{domains.languagecloud}|{ray_username}> is now disconnected from <@{user_id}>."
        )
        if is_sso:
            text = _(
                "Your LanguageCloud account *{ray_username}* is now disconnected from <@{user_id}>."
            )
        block_message = (
            text
            if ray_username
            else _("Your LanguageCloud account is now disconnected from <@{user_id}>.")
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
                        "text": _(
                            "You can use `connect` to connect your LanguageCloud account again."
                        ),
                    },
                },
            ],
        )


class SlackPermissionsMessage(SlackMessage):
    """Prompt the user to install the app again to grant user scope permissions,
    e.g. `chat:write` to allow the app to post/edit messages of the user's behalf.
    """

    def __init__(self, message: str) -> None:
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
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Allow permissions"),
                            },
                            "style": "primary",
                            "url": f"{domains.slack_ray_translator}/slack/install?user_scope=chat:write",
                            "action_id": "link",
                        }
                    ],
                },
            ],
        )

    @classmethod
    def auto_translate_variation(cls):
        return cls(
            "To have our app translate your messages by editing your original "
            "message instead of replying, you need to give the app extra permissions. "
            "Click the button below to do this."
        )


class JobStatusMessage(SlackMessage):
    """Message showing the status of a translation job."""

    def __init__(self, job: Job, client_id: str, job_prediction: str = "") -> None:
        job_status_block: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        "The job status for *{job.id}* is below:",
                    ),
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": _("*Status:*")},
                    {"type": "mrkdwn", "text": _(format_job_status(job.status))},
                    {"type": "mrkdwn", "text": _("*Source Language:*")},
                    {"type": "mrkdwn", "text": job.sl.name},
                    {"type": "mrkdwn", "text": _("*Target Language:*")},
                    {
                        "type": "mrkdwn",
                        "text": ", ".join(sorted([lang.name for lang in job.tl])),
                    },
                    {"type": "mrkdwn", "text": _("*Expected Completion Date:*")},
                    {
                        "type": "mrkdwn",
                        "text": format_job_due_date_slack(
                            job.target_date, job.status, traffic_light=True
                        ),
                    },
                ],
            },
            # TODO: Blocks.py translate
            job_link_block(job.uuid, client_id),
        ]
        if (
            job.status != "COMPLETED"
            and job.batches != "[]"
            and job.translated_file == []
        ):
            job_status_block.insert(
                3,
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Show In Progress Files"),
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
        if (
            job.status != "COMPLETED"
            and job.batches != "[]"
            and job.translated_file != []
        ):
            job_status_block.insert(
                3,
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Show In Progress Files"),
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
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Show Completed Files"),
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
                        },
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
                                "text": _("Show Completed Files"),
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
        job_link = f"<{get_job_url(job.uuid, client_id)}|*{job.id}*>"
        job_due_date = format_job_due_date_slack(
            job.target_date, job.status, traffic_light=True
        )
        job_detail_block: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        "The information for {job_link} is below:",
                    ),
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": _(
                            f"*Job Status:*\n{format_job_status(job.status)}",
                        ),
                    },
                    {
                        "type": "mrkdwn",
                        "text": _("*Group:*")
                        + f"\n{job.group.name if job.group else ''}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": _(
                            "*Due Date/Time*\n{job_due_date}",
                        ),
                    },
                    {"type": "mrkdwn", "text": _("*Reference:*\n{job.reference}")},
                    {
                        "type": "mrkdwn",
                        "text": _(
                            "*Source Language:*\n{job.sl.name}",
                        ),
                    },
                    {
                        "type": "mrkdwn",
                        "text": _("*Target Languages: ")
                        + f"*\n{', '.join(sorted([lang.name for lang in job.tl]))}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": _(
                            f"*Valdation*\n{'Yes' if job.validation else 'No'}",
                        ),
                    },
                    {
                        "type": "mrkdwn",
                        "text": _(
                            "*Project Manager*\n{job.project_manager.first_name} {job.project_manager.last_name}",
                        ),
                    },
                ],
            },
            job_link_block(job.uuid, client_id),
        ]
        if (
            job.status != "COMPLETED"
            and job.batches != "[]"
            and job.translated_file == []
        ):
            job_detail_block.insert(
                3,
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Show In Progress Files"),
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
        elif (
            job.status != "COMPLETED"
            and job.batches != "[]"
            and job.translated_file != []
        ):
            job_detail_block.insert(
                3,
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Show In Progress Files"),
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
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Show Completed Files"),
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
                        },
                    ],
                },
            )
        elif job.status == "COMPLETED":
            job_detail_block.insert(
                3,
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Show Completed Files"),
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
        upper_job_id = job_id.upper()
        super().__init__(_("Cannot find the job: *{upper_job_id}*"))


class JobStatusNoIdMessage(TextMessage):
    """Message to send when the user asks for a job status but has not given
    a TJ number.
    """

    def __init__(self) -> None:
        super().__init__(
            _(
                "To check the status of your job, type the reference number (e.g. TJ123456)."
            )
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
                        "text": _(
                            "*In Progress Jobs*\n*     {in_progress} Total Job(s)*",
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("View More"),
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
                            "text": _(
                                "*     {in_progress_count_24} job(s)* accepted in the last 24 hours",
                            ),
                        },
                        "accessory": {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "emoji": True,
                                "text": _("View More"),
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
                            "text": _(
                                "*     {in_progress_due} job(s)* due within 24 hours",
                            ),
                        },
                        "accessory": {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "emoji": True,
                                "text": _("View More"),
                            },
                            "action_id": "job_list",
                            "value": "IN_PROGRESS:DUE:24H",
                        },
                    },
                )

            if config.environment != Environment.production:
                if (predictions["on_time"]) > 0:
                    job_plural = (
                        "job is" if int(predictions["on_time"]) == 1 else "jobs are"
                    )
                    sections.append(
                        job_prediction_block(
                            (
                                "*     :large_green_circle: {value}"
                                + f"{job_plural}* predicted to be on-time"
                            ),
                            predictions["on_time"],
                        )
                    )
                if (predictions["late"]) > 0 or (predictions["over_due"]) > 0:
                    total_late = int(predictions["late"]) + int(predictions["over_due"])
                    job_plural = (
                        "job"
                        if int(predictions["late"]) + int(predictions["over_due"]) == 1
                        else "jobs"
                    )
                    sections.append(
                        job_prediction_block(
                            (
                                "*     :large_orange_circle: {value}"
                                + f" {job_plural}* may be behind schedule"
                            ),
                            total_late,
                        )
                    )
        if completed > 0 or all_jobs:
            sections.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "*Completed Jobs*\n*     {completed} job(s)* completed in the past 7 days",
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("View More"),
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
                        "text": _(
                            "*Validation*\n*     {validation} job(s)* currently being validated",
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("View More"),
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
                        "text": _(
                            "*Pending Quotes*\n*     {pending_quotes} quote(s)* pending",
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("View More"),
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
                        "text": _(
                            "*Order Now*\n*     {order_now} job(s)* to order",
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("View More"),
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
                        "text": _("No jobs found."),
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
                            "text": _("View All"),
                        },
                        "action_id": "all_summary",
                    },
                },
            )
        super().__init__(
            _("In Progress Jobs: {in_progress} jobs currently in progress..."),
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
        jobs_blocks: list[dict[str, Any]] = []

        # If there is any jobs result
        if jobs:
            for i, job in enumerate(jobs):
                job_text = _("*{job.id}*")
                if job.reference:
                    job_text += _("\nRef: {job.reference}")

                job_text += f"\n{job.sl.shortname.upper()} > {', '.join(lang.shortname.upper() for lang in job.tl)}"
                job_text += _("\nDue: ") + format_job_due_date_slack(
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
                                "text": _("View More Info"),
                            },
                            "action_id": "show_job_details",
                            "value": json.dumps({"id": job.id, "status": job.status}),
                        },
                    }
                )
                if (
                    job.status != "COMPLETED"
                    and job.batches != "[]"
                    and job.translated_file == []
                ):
                    jobs_blocks.append(
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": _("Show In Progress Files"),
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
                elif (
                    job.status != "COMPLETED"
                    and job.batches != "[]"
                    and job.translated_file != []
                ):
                    jobs_blocks.append(
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": _("Show In Progress Files"),
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
                                },
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": _("Show Completed Files"),
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
                                },
                            ],
                        },
                    )
                elif job.status == "COMPLETED":
                    jobs_blocks.append(
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": _("Show Completed Files"),
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
                elif job.status == "PENDING_QUOTES" or job.status == "ORDER_NOW":
                    jobs_blocks.append(
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": _("🔴 Cancel this job"),
                            },
                            "accessory": {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": _("Cancel"),
                                },
                                "action_id": "cancel_job",
                                "value": json.dumps(
                                    {"job_id": job.id, "job_action": "list"}
                                ),
                            },
                        }
                    )
                if formatted_job_prediction != "":
                    jobs_blocks.append(job_prediction_block(formatted_job_prediction))
        else:
            jobs_blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "No jobs found",
                        ),
                    },
                }
            )

        pagination_blocks: list[dict[str, Any]] = []
        if jobs and pagination.total_pages > 1:
            pagination_blocks.append({"type": "actions", "elements": []})
            if pagination.page > 1:
                pagination_blocks[0]["elements"].append(
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Show previous jobs"),
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
                            "text": _("Show more jobs"),
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

    def __init__(self, channel_id: str, timestamp: str, file_id: str = "") -> None:
        super().__init__(
            "Submit a new translation job",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "Please upload your files to translate in the message composer below, or alternatively, if you have already uploaded your files, click the *New translation job* button below"
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": (
                        [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": _("New translation job"),
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
                        ]
                        + (
                            [
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": _("Machine Translate"),
                                        "emoji": True,
                                    },
                                    "action_id": "document_mt_job",
                                    "style": "primary",
                                    "value": file_id,
                                }
                            ]
                            if file_id
                            else []
                        )
                    ),
                },
            ],
        )


class JobSubmitMessage(SlackMessage):
    """Message to send when a new job is submitted."""

    def __init__(self, new_job_form: NewJobForm) -> None:
        lang_str = ", ".join(f"*{lang.name}*" for lang in new_job_form.target_langs)
        super().__init__(
            _(
                "Your translation request has been submitted. You will be notified when a job number is assigned."
            ),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            ":tada: Your translation request has been submitted. You will be notified when a job number is assigned."
                        ),
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "The following files will be translated from *{new_job_form.source_lang.name}* to {lang_str}:"
                        ),
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "\n".join(
                            (f"• {file.title}" for file in new_job_form.files)
                        ),
                    },
                },
            ],
        )


class InsightsMessage(SlackMessage):
    def __init__(self, message: str):
        super().__init__(
            _(":bulb: Here are your insights"),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(":bulb: *Here are your insights*"),
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


class JobCreationMessage(SlackMessage):
    """A job TJ number is created after submitting a new job (from API v3 callback)."""

    def __init__(self, job_id: str = "") -> None:
        super().__init__(
            "New Job Created",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            ":tada: A new translation job has been created with the job number: `{job_id}`"
                        ),
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _("🔴 Cancel your job"),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Cancel"),
                        },
                        "action_id": "cancel_job",
                        "value": json.dumps({"job_id": job_id, "job_action": "list"}),
                    },
                },
            ],
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
                            "text": _("Download"),
                            "emoji": False,
                        },
                        "style": "primary",
                        "action_id": f"link_{idx}",
                        "url": translated_file["download_url"],
                    },
                }
            )
        super().__init__(
            _(
                "Some of your files are translated and ready to be downloaded ({job_id})"
            ),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "Some of your files are translated and ready to be downloaded (*{job_id}*)",
                        ),
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": _("*File:*\n{source_file}")},
                        {
                            "type": "mrkdwn",
                            "text": _(
                                "*Source Language:*\n{source_lang}",
                            ),
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
                        "text": _("Hi there:wave: \n\nHow can we help?"),
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "🔍 Search allows you to search for specific Translation Jobs (TJs). "
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {"type": "plain_text", "text": _("Search")},
                        "action_id": "job_search",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            ":vertical_traffic_light: Jobs provides an update on the status of recently submitted jobs."
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {"type": "plain_text", "text": _("Jobs")},
                        "action_id": "all_summary",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "🗂️ Click 'New translation job' to select documents uploaded through the message box below. Note this will send the selected document off for translation."
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("New translation job"),
                        },
                        "action_id": "quote",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            ":bar_chart: Insights uses AI to gather and show data about your translation experience"
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {"type": "plain_text", "text": _("Insights")},
                        "action_id": "report_insights",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            ":globe_with_meridians: View your LanguageCloud connection."
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {"type": "plain_text", "text": _("Info")},
                        "action_id": "account_info",
                    },
                },
                {
                    "type": "section",
                    "block_id": "sectionBlockWithButton",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(":Seedling: Connect your LanguageCloud account"),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Connect"),
                        },
                        "action_id": "connect_info",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _("🔴 Cancel your job"),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Cancel"),
                        },
                        "action_id": "cancel_job",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(":blue_book: Learn The Basics"),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Help Centre"),
                        },
                        "url": "https://help.strakertranslations.com/hc/en-us/categories/10020714644633-Apps",
                        "action_id": "link_2",
                    },
                },
                {"type": "divider"},
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": _(
                                ":question: Need more information? Ask our chat bot below."
                            ),
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
                        "text": _(
                            "Please upload your files to translate in the message composer below, or alternatively, if you have already uploaded your files, click the *New translation job* button below"
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("New translation job"),
                            },
                            "style": "primary",
                            "action_id": "new_job",
                        }
                    ],
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
            text = _("Your Slack workspace is connected with: {super_group_names_str}.")
            workspace_block = {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        "Your Slack workspace is connected with: *{super_group_names_str}*.",
                    ),
                },
            }
        else:
            text = _("Your Slack workspace is not connected with an organisation yet.")
            workspace_block = {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            }
        # Next get Slack user - LanguageCloud account info.
        account_blocks: list[dict[str, Any]] = []
        if ray_connection is not None and ray_connection.client is not None:
            text = _(
                "Your connected LanguageCloud account is: <{domains.languagecloud}|{ray_connection.client.username}>"
            )
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
                        "text": _(
                            "Click this button to connect your LanguageCloud account."
                        ),
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
                                "text": _("Connect LanguageCloud account"),
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
    """Invalid /straker command."""

    def __init__(self) -> None:
        super().__init__(
            _(
                ":no_entry_sign: Invalid command. Type `/straker help` for a list of valid commands."
            )
        )


class ClientApprovedMessage(TextMessage):
    """A group admin approved a new client in Slack."""

    def __init__(self, approved_client: str) -> None:
        super().__init__(
            _("The user {approved_client} has been approved to join your group(s).")
        )


class ClientAlreadyApprovedMessage(TextMessage):
    """A group admin approved a new client in Slack, but the client was already
    approved.
    """

    def __init__(self, approved_client: str) -> None:
        super().__init__(_("The user {approved_client} has already been approved."))


class JobQuotedMessage(SlackMessage):
    def __init__(self, quote: Quote) -> None:
        job_url = get_job_url(quote.uuid, quote.client_id)
        super().__init__(
            _("Pending Quote: Straker Job Reference {quote.id}"),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "*<{job_url}|Straker Job Reference {quote.id}>*",
                        ),
                    },
                },
            ]
            + quote_message_block(quote, job_url),
        )


class SsoConnectionInfoMessage(SlackMessage):
    """The current Slack - LanguageCloud connection details."""

    def __init__(
        self,
        ray_connection: RayConnection,
    ) -> None:
        if ray_connection.client is not None:
            text = _(
                "Your connected LanguageCloud account is: *{ray_connection.client.username}*."
            )

        msg = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Login to LanguageCloud"),
                        },
                        "style": "primary",
                        # TODO: ray_connection.client could be None
                        "url": encrpyt_slack_sso_token(ray_connection.client.username),
                        "action_id": "login",
                    }
                ],
            },
        ]
        super().__init__(
            "Login to LanguageCloud",
            msg,
        )


# -----------------------------------------------------------------------------
# Ray event messages
# -----------------------------------------------------------------------------


class ClientSignupEventMessage(SlackMessage):
    def __init__(self, event: ClientSignupEvent) -> None:
        self.event = event
        user_url = f"<{domains.languagecloud}|{event.username}>"
        super().__init__(
            "Thank you for signing up to LanguageCloud :tada:",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "Thank you for signing up to LanguageCloud {user_url} :tada:",
                        ),
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "A notification has been sent to your Admins who will approve your account. You will be notified again once this has been approved."
                        ),
                    },
                },
            ],
        )


class ClientSignupEventAdminMessage(SlackMessage):
    def __init__(self, event: ClientSignupEvent, groups: list[ClientGroup]) -> None:
        self.event = event
        user_str = f"{event.first_name} {event.last_name} ({event.email})"
        super().__init__(
            _("A new user has signed up for a LanguageCloud account: {user_str}"),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "A new user has signed up for a LanguageCloud account:\n{user_str}",
                        ),
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "Before this user can use Straker Translate for Slack, they require approval for the groups they should be associated with:"
                        ),
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _("*Group(s)*:\n")
                        + "\n".join(group.label for group in groups),
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "To approve this user please click the approve button below, or alternatively if you need to change anything, please log into LanguageCloud to edit their permissions."
                        ),
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
                                "text": _("Approve"),
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
                                "text": _("Log into LanguageCloud"),
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
                        "text": _(
                            ":raised_hands: Your LanguageCloud groups have been approved by an Admin:\n\n{groups_text}",
                        ),
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            ":white_check_mark: You can now access all the features within Straker Translate."
                        ),
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "Use `/straker help` to show some ideas of what you can do."
                        ),
                    },
                },
            ],
        )


class JobStatusChangedEventMessage(SlackMessage):
    def __init__(self, client_id: str, job_uuid: str, job_id: str, status: str) -> None:
        status_formatted = format_job_status(status)
        super().__init__(
            _(
                "Your translation job {job_id} has changed status to: {status_formatted}"
            ),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "Your translation job *{job_id}* has changed status to: {status_formatted}",
                        ),
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
            _("Your files for {job_id} are ready to download :white_check_mark:"),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "Your files for *{job_id}* in *{target_lang_text}* are ready to download :white_check_mark:",
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Show Completed Files"),
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
                        "text": _(
                            "Please log into LanguageCloud below to access your completed files."
                        ),
                    },
                },
                job_link_block(job_uuid, client_id),
            ],
        )


class JobCancelledEventMessage(SlackMessage):
    def __init__(self, client_id: str, job_uuid: str, job_id: str) -> None:
        job_url = f"<{get_job_url(job_uuid, client_id)}|{job_id}>"
        super().__init__(
            _("Your translation job {job_id} has been cancelled"),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "Your translation job *{job_url}* has been cancelled.",
                        ),
                    },
                },
            ],
        )


class JobQuoteAcceptedEventMessage(SlackMessage):
    def __init__(self, event: JobQuoteAcceptedEvent) -> None:
        target_date = format_datetime_slack(event.target_date)
        id = event.id
        super().__init__(
            _(
                "Quote Accepted for {id}. Your job will be completed before {target_date}."
            ),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            ":clap: Quote Accepted for *{id}*. Your job will be completed before {target_date}."
                        ),
                    },
                },
                job_link_block(event.uuid, event.client_id),
            ],
        )


class JobQuoteCancelledEventMessage(SlackMessage):
    def __init__(self, client_id: str, job_uuid: str, job_id: str) -> None:
        job_url = f"<{get_job_url(job_uuid, client_id)}|{job_id}>"
        super().__init__(
            _("We have cancelled the quote for {job_id}."),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "We have cancelled the quote for *{job_url}*.",
                        ),
                    },
                },
            ],
        )


class JobQuotedEventMessage(SlackMessage):
    def __init__(self, event: JobQuoteCreatedEvent) -> None:
        job_url = f"<{get_job_url(event.uuid, event.client_id)}|{_('Straker Job Reference')} {event.id}>"
        super().__init__(
            _(
                "Your quote is now ready :raised_hands: Straker Job Reference {event.id}"
            ),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "Your quote is now ready :raised_hands:\n**",
                        ),
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
                        "text": _(message),
                    },
                },
            ],
        )


class BatchListMessage(SlackMessage):
    """Message showing the list of in progress files."""

    def __init__(self, job: Job, client_id: str) -> None:
        title = _("The in progress file list for *{job.id}* is below:")

        job_file_block: list[dict[str, Any]] = []
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
                and batch["batch_status"]
                in ("TRANSLATED", "REVIEWED", "QA_REVIEWED", "VALIDATED", "VALIDATED 2")
            ):
                job_text += f"\n    - {job.status.upper()} - {batch['batch_status'].upper()} - <{download_prefix + batch['generated_file']}|DOWNLOAD>"
            else:
                job_text += (
                    f"\n    - {job.status.upper()} - {batch['batch_status'].upper()}"
                )

        job_file_block.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": job_text,
                },
            }
        )

        pagination_blocks: list[dict[str, Any]] = []
        if job.pagination.total_pages > 1:
            pagination_blocks.append({"type": "actions", "elements": []})
            if job.pagination.page > 1:
                pagination_blocks[0]["elements"].append(
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Show previous files"),
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
                            "text": _("Show more files"),
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
        title = _("The completed file list for *{job.id}* is below:")
        job_file_block: list[dict[str, Any]] = []
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
                        "text": _("Download"),
                    },
                    "url": x["download_url"],
                    "action_id": "link",
                    "style": "primary",
                },
            }
            job_file_block.insert(2, url)

        pagination_blocks: list[dict[str, Any]] = []
        if job.f_pagination.total_pages > 1:
            pagination_blocks.append({"type": "actions", "elements": []})
            if job.pagination.page > 1:
                pagination_blocks[0]["elements"].append(
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Show previous files"),
                            "emoji": True,
                        },
                        "action_id": "file_list_0",
                        "value": json.dumps(
                            {
                                "id": job.id,
                                "page": job.f_pagination.page - 1,
                                "page_size": job.f_pagination.rows_per_page,
                                "replace_original": True,
                            }
                        ),
                    }
                )
            if job.f_pagination.page < job.f_pagination.total_pages:
                pagination_blocks[0]["elements"].append(
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Show more files"),
                            "emoji": True,
                        },
                        "action_id": "file_list_1",
                        "value": json.dumps(
                            {
                                "id": job.id,
                                "page": job.f_pagination.page + 1,
                                "page_size": job.f_pagination.rows_per_page,
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
    def __init__(self, plan: str | None) -> None:
        if not is_min_langugagecloud_plan(plan, "Essentials"):
            message = "The insights feature is only avaiable on the Growth and Enterprise plans."
        else:
            message = "You can use the message pane below to type your insights request using natural language. Get turn around times, cost, or validation quality. An example:\n>Can you tell me how many jobs have been delivered on time in the last 30 days"
        super().__init__(
            _(":bulb: Here are your insights"),
            [{"type": "section", "text": {"type": "mrkdwn", "text": _(message)}}],
        )


class JobTargetsNoIdMessage(TextMessage):
    """Message to send when the user asks for a job targets but has not given
    a TJ number.
    """

    def __init__(self) -> None:
        super().__init__(
            _(
                "To check the targets of your job, type the reference number (e.g. TJ123456)."
            )
        )


class JobTargetLangMessage(SlackMessage):
    """Message showing the list of translation files."""

    def __init__(self, job: Job, client_id: str) -> None:
        if job.status == "PENDING_QUOTES":
            title = _("*{job.id}* waiting for quotation.")
        elif job.status == "ORDER_NOW":
            title = _("Job *{job.id}* waiting for order.")
        else:
            title = _("Job *{job.id}* no targets information.")
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


class AutoTranslationMessage(SlackMessage):
    def __init__(
        self,
        source_text: str | None,
        source_language: str,
        translations: list[tuple[str, str]],
        scores: list[tuple[str, float]] | None = None,
    ) -> None:
        """Slack message template for an auto-translated message

        Args:
            source_text (str | None): The original source text. If empty, do not
                the source text.
            source_language (str): The source language, e.g. "en", "de".
            translations (list[tuple[str, str]]): A list of translations.
                Each element is a 2-tuple with the target language and translated text.
        """
        self.source_text = source_text
        self.source_language = source_language
        self.translations = translations
        self.scores = scores
        # TODO what happens when no translations?
        text = source_text or (self.translations[0][1] if self.translations else "")
        super().__init__(text, self.generate_blocks())

    def generate_blocks(self) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if self.source_text:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": self.source_text},
                }
            )
        for target_lang, translated in self.translations:
            quoted_translated = "\n".join(
                ["> " + line for line in translated.split("\n")]
            )
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": quoted_translated},
                }
            )
        target_langs = [
            get_auto_translate_language_name(t[0]) for t in self.translations
        ]
        target_langs_string = format_strings_display(target_langs, and_string="&")
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "plain_text",
                        "text": f"Translated to {target_langs_string} using Straker AI",
                    }
                ],
            }
        )
        if self.scores:
            source_lang_full = get_auto_translate_language_name(self.source_language)
            for lang, score in self.scores:
                target_lang_full = get_auto_translate_language_name(lang)
                score_text = f":large_green_circle: {source_lang_full} → {target_lang_full} is accurate"
                if score < 0.5:
                    score_text = f":large_red_circle: {source_lang_full} → {target_lang_full} is inaccurate"
                elif score < 0.8:
                    score_text = f":large_orange_circle: {source_lang_full} → {target_lang_full} might need checking"
                blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "plain_text",
                                "text": score_text,
                            }
                        ],
                    }
                )
        return blocks

    def add_translation_scores(
        self, scores: list[tuple[str, float]] | None
    ) -> "AutoTranslationMessage":
        """Return a new message with translation scores added."""
        return AutoTranslationMessage(
            self.source_text, self.source_language, self.translations, scores
        )


class MachineTranslationMessage(SlackMessage):
    """Message showing the list of translation files."""

    def __init__(self, tl: str, sl: str, mt_text: str) -> None:
        mt_label = _("Machine translation result:")
        super().__init__(
            f"{mt_label} {mt_text}",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{mt_label}*",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{mt_text} ({sl}-{tl}) ",
                    },
                },
            ],
        )


class SrtTranslateMessage(SlackMessage):
    """Message to allow user to select language and submit for machine translation"""

    def __init__(self, task_uuid: str) -> None:
        title = _("Please select the target language for translation")
        language_options = get_auto_translate_language_options()
        # create message which contains the output_file of the submit button and contains a input element which is a multi select for language
        super().__init__(
            title,
            [
                {
                    "type": "input",
                    "block_id": task_uuid,
                    "label": {
                        "type": "plain_text",
                        "text": _("Select language"),
                    },
                    "element": {
                        "type": "static_select",
                        "placeholder": {
                            "type": "plain_text",
                            "text": _("Choose language"),
                        },
                        "options": language_options,
                        "action_id": "language_mt_options",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Submit"),
                                "emoji": False,
                            },
                            "action_id": "srt_translate",
                            "style": "primary",
                            "value": task_uuid,
                        },
                    ],
                },
            ],
        )


class DocumentMTJobMessage(SlackMessage):
    """Message to allow user to select language and submit for machine translation"""

    def __init__(self, output_file: str) -> None:
        title = _("Please select the target language for translation")
        language_options = get_auto_translate_language_options()
        # create message which contains the output_file of the submit button and contains a input element which is a multi select for language
        super().__init__(
            title,
            [
                {
                    "type": "input",
                    "block_id": output_file,
                    "label": {
                        "type": "plain_text",
                        "text": _("Select language"),
                    },
                    "element": {
                        "type": "static_select",
                        "placeholder": {
                            "type": "plain_text",
                            "text": _("Choose language"),
                        },
                        "options": language_options,
                        "action_id": "language_mt_options",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Submit"),
                                "emoji": False,
                            },
                            "action_id": "document_mt_submit",
                            "style": "primary",
                            "value": output_file,
                        },
                    ],
                },
            ],
        )


class JobTranscribedEventMessage(SlackMessage):

    def __init__(self, task_uuid: str) -> None:
        title = _("We have *transcribed* your file and SRT can be downloaded below.")
        # create message which contains the output_file
        super().__init__(
            title,
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": title,
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Download"),
                                "emoji": False,
                            },
                            "action_id": "download_transcribed_file",
                            "style": "primary",
                            "value": task_uuid,
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Translate"),
                                "emoji": False,
                            },
                            "action_id": "show_srt_translate_form",
                            "value": task_uuid,
                        },
                    ],
                },
            ],
        )


class TranscriptionMessage(TextMessage):
    def __init__(self) -> None:
        super().__init__(_("⏱️ Please wait a moment and we will transcribe your file"))


class InvalidMTResultMessage(TextMessage):
    """The user does not get MT result."""

    def __init__(self) -> None:
        super().__init__("Error occurred while translating your message")


class CancelJobMessage(SlackMessage):
        """Message with a button to open the cancel job modal."""

        def __init__(self, channel_id: str, timestamp: str) -> None:
            super().__init__(
                "Cancel a translation job",
                [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": _(
                                "Click the *Cancel translation job* button below"
                            ),
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": _("Cancel translation job"),
                                    "emoji": True,
                                },
                                "action_id": "cancel_job",
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


class CancelTJMessage(SlackMessage):
    """A summary of the client's jobs, number of jobs in each status. Has buttons
    to display the individual job IDs for each status and timeframe.
    """

    def __init__(self, channel_id: str, jobdetail) -> None:
        target_labels = [target.label for target in jobdetail['targetlang']]
        super().__init__(
            "Cancel a translation job",
            [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f" Cancel  {jobdetail['job_id']}",
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Status:*\n {jobdetail['status']}"
                        }
                    ]
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Source:*\n {jobdetail['sourcelang'].label}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Target:*\n {', '.join(target_labels)}"
                        }
                    ]
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Please confirm to cancel this job."
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Cancel Job",
                        },
                        "value": json.dumps(
                            {"job_id": jobdetail['job_id'], "job_action": "list"}
                        ),
                        "action_id": "cancel_job",
                    }
                },
            ],
        )


class AutoTranslateSettingsChangedMessage(TextMessage):
    """Message to send when the user changes their auto-translate settings."""

    def __init__(self, channel_id: str, langs: list[str], display_format: str) -> None:
        langs_string = format_strings_display(
            [get_auto_translate_language_name(lang) for lang in langs], and_string="and"
        )
        display_format_string = (
            "thread replies" if display_format == "thread" else "messages"
        )
        super().__init__(
            f"The bot will respond to messages sent in <#{channel_id}> which will be translated into {langs_string} through {display_format_string} in real-time."
        )


class AutoTranslateSettingsDisabledMessage(TextMessage):
    """Message to send when the user disable/enable their auto-translate settings."""

    def __init__(self, channel_id: str) -> None:
        super().__init__(f"<#{channel_id}> Translation settings have been disabled.")


class RequiresMtTokenMessage(SlackMessage):

    def __init__(self, tokens: int, required_tokens: int) -> None:
        title = _(
            "❗❗You have *{tokens} MT characters* on your account. This job requires *{required_tokens} MT characters*. Please purchase a MT bundle.❗❗"
        )
        if tokens <= 0 and required_tokens == 1:
            title = _(
                "❗❗Your group account has no MT characters. Please purchase a MT bundle.❗❗"
            )
        elif tokens <= 0:
            title = _(
                "❗❗Your group account has no MT characters. This job requires *{required_tokens} MT characters*. Please purchase a MT bundle.❗❗"
            )

        # create message which contains the output_file
        super().__init__(
            title,
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": title,
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Purchase MT Bundle"),
                                "emoji": False,
                            },
                            "action_id": "button-action",  # Add this line
                            "url": f"{domains.languagecloud}/checkout/characters",
                        },
                    ],
                },
            ],
        )


class RequiresMtTokenAdminMessage(SlackMessage):

    def __init__(self, tokens: int, required_tokens: int, admin=False) -> None:
        title = _(
            "❗❗You have *{tokens} MT characters* on your group account. This job requires *{required_tokens} MT characters*. Please contact your group admin to purchase more❗❗"
        )
        if tokens <= 0 and required_tokens == 1:
            title = _(
                "❗❗Your group account has no MT characters. Please contact your group admin to purchase more❗❗"
            )
        elif tokens <= 0:
            title = _(
                "❗❗Your group account has no MT characters. This job requires *{required_tokens} MT characters*. Please contact your group admin to purchase more❗❗"
            )
        # create message which contains the output_file
        super().__init__(
            title,
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": title,
                    },
                },
            ],
        )


class CancelJobMessage(SlackMessage):
    """Message with a button to open the cancel job modal."""

    def __init__(self, channel_id: str, timestamp: str) -> None:
        super().__init__(
            "Cancel a translation job",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _("Click the *Cancel translation job* button below"),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Cancel translation job"),
                                "emoji": True,
                            },
                            "action_id": "cancel_job",
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


class DocMtMessage(SlackMessage):
    """Message verify consumer event response"""

    def __init__(self) -> None:
        super().__init__(
            _("Verify the translation"),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _("Error occurred while translating your document"),
                    },
                }
            ],
        )


class CancelTJMessage(SlackMessage):
    """A summary of the client's jobs, number of jobs in each status. Has buttons
    to display the individual job IDs for each status and timeframe.
    """

    def __init__(self, channel_id: str, jobdetail) -> None:
        target_labels = [target.label for target in jobdetail["targetlang"]]
        super().__init__(
            "Cancel a translation job",
            [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f" Cancel  {jobdetail['job_id']}",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Status:*\n {jobdetail['status']}"}
                    ],
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Source:*\n {jobdetail['sourcelang'].label}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Target:*\n {', '.join(target_labels)}",
                        },
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Please confirm to cancel this job.",
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Cancel Job",
                        },
                        "value": json.dumps(
                            {"job_id": jobdetail["job_id"], "job_action": "list"}
                        ),
                        "action_id": "cancel_job",
                    },
                },
            ],
        )
