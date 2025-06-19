"""Slack Messages templates."""

from typing import Any, Dict, List
import json

import langcodes
from app.slack.select_options import (
    get_auto_translate_language_options,
)
from ray_sdk.api.v3.models import Job, Pagination, Quote

from .models import NewJobForm
from .blocks import (
    job_link_block,
    job_summary_string,
    quote_message_block,
    job_prediction_block,
    verify_quote_blocks,
)
from ...ray.events.models import (
    ClientSignupEvent,
    JobQuoteCreatedEvent,
    ClientGroup,
    JobQuoteAcceptedEvent,
)

from ...ray.utils import (
    get_job_url,
    format_job_status,
    format_datetime_slack,
    format_job_due_date_slack,
    format_job_prediction,
    is_ibm_enterprise,
    is_min_langugagecloud_plan,
)
from ...ray.settings import get_auto_translate_language_name
from ..utils import format_strings_display
from ...config import config, domains, Environment
from ...auth.connector import (
    RayClient,
    RayConnection,
    RayContext,
    get_language_cloud_connect_url,
    encrpyt_slack_sso_token,
)
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
        team_id: str | None,
        enterprise_id: str | None,
        channel_id: str,
        prompt_login: bool = True,
    ) -> None:
        tadaEmoji = f":tada:"
        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _("Welcome to Straker Translate for Slack! {tadaEmoji}"),
                },
            }
        ]
        if prompt_login and team_id:
            blocks.extend(
                [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": _(
                                "Connect your account to get details about your translation jobs."
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
                                    "text": _("Connect account"),
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
    AI_HELP = "ai_help"
    QUALITY_EVALUATION = "quality_evaluation"
    HUMAN_TRANSLATION = "human_translation"

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
        block_text = "In order to use the Straker Translate features, please login. Click this button below;"
        if variation == self.GET_JOB:
            block_text = "Connect your account to view your jobs."
        elif variation == self.NEW_JOB:
            block_text = "Connect your account to submit a new translation job."
        elif variation == self.INSIGHTS:
            block_text = "Connect your account to view your insights."
        elif variation == self.CANCEL_JOB:
            block_text = "Connect your account to cancel your job."
        elif variation == self.QUALITY_EVALUATION:
            block_text = (
                "Connect your account to evaluate the quality of your translation."
            )
        elif variation == self.HUMAN_TRANSLATION:
            block_text = "Connect your account to perform human translation."
        elif isinstance(ray_client, RayClient):
            user_details = f"<<{domains.languagecloud}|{ray_client.username}>>"
            block_text = (
                "Your connected account is: {user_details}. "
                + "\nYou can connect a different account by clicking this button."
            )
            if ray_client.sso:
                block_text = "Your connected account is: *{ray_client.username}*."
        msg: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": _(block_text)},
            },
        ]
        if not isinstance(ray_client, RayClient):
            if is_ibm_enterprise(enterprise_id):
                msg.append(
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": _("Direct Login"),
                                },
                                "style": "primary",
                                "action_id": "login_sso",
                            },
                        ],
                    }
                )
            else:
                msg.append(
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": _("Connect account"),
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
            "Connect your account",
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

    def __init__(self, user_id: str, ray_connection: RayConnection) -> None:
        waveEmoji = f":wave:"
        is_verify_enabled = (
            ray_connection.super_group[0].enable_verify_in_slack
            if ray_connection
            else False
        )
        super().__init__(
            "Welcome back :wave:",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "plain_text",
                        "emoji": True,
                        "text": _(
                            "Welcome {waveEmoji} \n\nChoose an option below to get started."
                        ),
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": _(":dart: Learn Direct MT")},
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Direct MT Help"),
                        },
                        "url": "https://help.straker.ai/en/docs/direct-machine-translation-mt-in-straker-translate-app-for-slack",
                        "action_id": "link_direct_mt",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(":books: Learn AI Channel Translations"),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Channel Translations Help"),
                        },
                        "url": "https://help.straker.ai/en/docs/how-to-use-channel-translations",
                        "action_id": "link_channel_translations",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(":page_with_curl: Learn Document MT"),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Document MT Help"),
                        },
                        "url": "https://help.straker.ai/en/docs/ai-translate-for-documents-in-straker-translate-app-for-slack",
                        "action_id": "link_document_mt",
                    },
                },
                *(
                    [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": _(
                                    ":sports_medal: AI Translate your content and receive translation quality scores, then verify with Straker to send for human verification"
                                ),
                            },
                            "accessory": {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": _("Quality Evaluation"),
                                },
                                # "url": "https://help.strakertranslations.com/hc/en-us/articles/35943216049945-Instant-Document-Machine-Translation-AI-Translate-in-Straker-Translate-App-for-Slack",
                                "action_id": "verify_help",
                            },
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": _(
                                    ":bust_in_silhouette: Translating content from one language to another while preserving meaning and context"
                                ),
                            },
                            "accessory": {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": _("Human Translation"),
                                },
                                "action_id": "human_help",
                            },
                        },
                    ]
                    if is_verify_enabled
                    else []
                ),
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "🔍 Search allows you to search for specific Translation Jobs (TJs)."
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
                        "text": "🔴 " + _("Cancel your job"),
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
                        "url": "https://help.straker.ai/en/docs/workplace-apps#straker-translate-app-for-slack",
                        "action_id": "link_2",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*<https://help.straker.ai/en/docs/straker-translate-functions|{_('Show more options')}>*",
                    },
                },
            ],
        )


class SuccessfulLoginMessage(SlackMessage):
    """Message to send after a user successfully connects their LanguageCloud
    account.
    """

    def __init__(
        self, user_id: str, ray_username: str, ray_connection: RayConnection
    ) -> None:
        waveEmoji = f":wave:"
        is_verify_enabled = (
            ray_connection.super_group[0].enable_verify_in_slack
            if ray_connection
            else False
        )
        super().__init__(
            ":white_check_mark: Login was successful!",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "plain_text",
                        "emoji": True,
                        "text": _(
                            "Welcome {waveEmoji} \n\nChoose an option below to get started."
                        ),
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": _(":dart: Learn Direct MT")},
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Direct MT Help"),
                        },
                        "url": "https://help.straker.ai/en/docs/direct-machine-translation-mt-in-straker-translate-app-for-slack",
                        "action_id": "cf",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(":books: Learn AI Channel Translations"),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Channel Translations Help"),
                        },
                        "url": "https://help.straker.ai/en/docs/how-to-use-channel-translations",
                        "action_id": "link_channel_translations",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(":page_with_curl: Learn Document MT"),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Document MT Help"),
                        },
                        "url": "https://help.straker.ai/en/docs/ai-translate-for-documents-in-straker-translate-app-for-slack",
                        "action_id": "link_document_mt",
                    },
                },
                *(
                    [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": _(
                                    ":sports_medal: AI Translate your content and receive translation quality scores, then verify with Straker to send for human verification"
                                ),
                            },
                            "accessory": {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": _("Quality Evaluation"),
                                },
                                # "url": "https://help.strakertranslations.com/hc/en-us/articles/35943216049945-Instant-Document-Machine-Translation-AI-Translate-in-Straker-Translate-App-for-Slack",
                                "action_id": "verify_help",
                            },
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": _(
                                    ":bust_in_silhouette: Translating content from one language to another while preserving meaning and context"
                                ),
                            },
                            "accessory": {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": _("Human Translation"),
                                },
                                "action_id": "human_help",
                            },
                        },
                    ]
                    if is_verify_enabled
                    else []
                ),
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
                        "text": "🔴 " + _("Cancel your job"),
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
                        "url": "https://help.straker.ai/en/docs/workplace-apps#straker-translate-app-for-slack",
                        "action_id": "link_2",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*<https://help.straker.ai/en/docs/straker-translate-functions|{_('Show more options')}>*",
                    },
                },
            ],
        )


class LogoutMessage(SlackMessage):
    """Message with a button disconnect a user's LanguageCloud account."""

    def __init__(self, ray_client: RayClient) -> None:
        user_details = f"<{domains.languagecloud}|{ray_client.username}>"
        text = _("Click this button to disconnect your account: {user_details}.")
        if ray_client.sso:
            text = _(
                "Click this button to disconnect your account: *{ray_client.username}*."
            )
        super().__init__(
            "Disconnect your account",
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
                                "text": _("Disconnect account"),
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
        user_details = f"<{domains.languagecloud}|{ray_username}>"
        user_link = f"<@{user_id}>"
        text = _("Your account {user_details} is now disconnected from {user_link}.")
        if is_sso:
            text = _(
                "Your account *{ray_username}* is now disconnected from {user_link}."
            )
        block_message = (
            text
            if ray_username
            else _("Your account is now disconnected from {user_link}.")
        )
        super().__init__(
            "Your account is now disconnected.",
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
                            "You can use `connect` to connect your account again."
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

    def __init__(
        self, job: Job, client_id: str, is_ibm: bool, job_prediction: str = ""
    ) -> None:
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
        ]
        if not is_ibm:
            job_status_block.append(job_link_block(job.uuid, client_id))
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

    def __init__(
        self, job: Job, client_id: str, is_ibm: bool, job_prediction: str = ""
    ) -> None:
        job_link = (
            f"<{get_job_url(job.uuid, client_id)}|*{job.id}*>"
            if not is_ibm
            else f"*{job.id}*"
        )
        pm_details = f"{job.project_manager.first_name} {job.project_manager.last_name}"
        job_due_date = format_job_due_date_slack(
            job.target_date, job.status, traffic_light=True
        )
        source_lang = _(job.sl.name)
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
                        "text": _(f"*Job Status:*\n")
                        + f"{format_job_status(job.status)}",
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
                            "*Source Language:*\n{source_lang}",
                        ),
                    },
                    {
                        "type": "mrkdwn",
                        "text": _("*Target Languages:")
                        + f"*\n{', '.join(sorted([lang.name for lang in job.tl]))}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": _(
                            f"*Validation*\n{'Yes' if job.validation else 'No'}",
                        ),
                    },
                    {
                        "type": "mrkdwn",
                        "text": _(
                            "*Project Manager*\n{pm_details}",
                        ),
                    },
                ],
            },
        ]
        if not is_ibm:
            job_detail_block.append(job_link_block(job.uuid, client_id))

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
                                "*     {emorji} {value}"
                                + f" {job_plural}* predicted to be on-time"
                            ),
                            predictions["on_time"],
                            ":large_green_circle:",
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
                                "*     {emorji} {value}"
                                + f" {job_plural}* may be behind schedule"
                            ),
                            total_late,
                            ":large_orange_circle:",
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
                    job.status == "PENDING_QUOTES"
                    or job.status == "ORDER_NOW"
                    or job.status == "LEAD"
                ):
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
                elif (
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

    def __init__(
        self,
        channel_id: str,
        timestamp: str,
        files: list[dict[str, Any]],
        is_verify_enabled: bool = False,
    ) -> None:
        files_dict = [{"id": f["id"], "title": f["title"]} for f in files]
        message_blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        "Please upload your files to translate in the message composer below, or alternatively, if you have already uploaded your files, click;\n\n"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        "*AI Translation* - AI translate content from one language into multiple languages\n\n"
                    ),
                },
                "accessory": {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "emoji": True,
                        "text": _("AI Translation"),
                    },
                    "action_id": "document_mt_job",
                    "value": json.dumps(
                        {"files": files_dict, "channel_id": channel_id}
                    ),
                },
            },
        ]

        if is_verify_enabled:
            message_blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "*Quality Evaluation* - AI translate your content and receive translation quality scores, then verify with Straker to send for human verification"
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Quality Evaluation"),
                        },
                        "action_id": "evaluate_job",
                        "style": "primary",
                        "value": json.dumps(
                            {
                                "files": files_dict,
                                "channel_id": channel_id,
                            }
                        ),
                    },
                },
            )
            message_blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "*Human Translation* - Translating content from one language to another while preserving meaning and context."
                        ),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Human Translation"),
                        },
                        "action_id": "evaluate_job",
                        "value": json.dumps(
                            {
                                "files": files_dict,
                                "channel_id": channel_id,
                                "job_type": "human",
                            }
                        ),
                    },
                },
            )

        super().__init__(
            "Submit a new job",
            message_blocks,
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

    def __init__(self, job_id: str = "", is_auto_quote: bool = False) -> None:
        tadeEmoji = f":tada:"
        quote_message = _(
            "Human translation is currently not supported, please continue to use Translate@IBM for human translation requests until further notice."
        )
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        _(
                            "{tadeEmoji} A new translation job has been created with the job number: `{job_id}`"
                        )
                        if is_auto_quote
                        else quote_message
                    ),
                },
            },
        ]
        if is_auto_quote:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🔴 " + _("Cancel your job"),
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
                }
            )
        super().__init__("New Job Created", blocks)


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

    def __init__(self, context: RayContext) -> None:
        ray_connection = context.ray
        is_verify_enabled = (
            ray_connection.super_group[0].enable_verify_in_slack
            if ray_connection
            else False
        )
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
                    "text": {"type": "mrkdwn", "text": _(":dart: Learn Direct MT")},
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Direct MT Help"),
                        },
                        "url": "https://help.straker.ai/en/docs/direct-machine-translation-mt-in-straker-translate-app-for-slack",
                        "action_id": "link_direct_mt",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(":books: Learn AI Channel Translations"),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Channel Translations Help"),
                        },
                        "url": "https://help.straker.ai/en/docs/how-to-use-channel-translations",
                        "action_id": "link_channel_translations",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(":page_with_curl: Learn Document MT"),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("Document MT Help"),
                        },
                        "url": "https://help.straker.ai/en/docs/ai-translate-for-documents-in-straker-translate-app-for-slack",
                        "action_id": "link_document_mt",
                    },
                },
                *(
                    [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": _(
                                    ":sports_medal: AI Translate your content and receive translation quality scores, then verify with Straker to send for human verification"
                                ),
                            },
                            "accessory": {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": _("Quality Evaluation"),
                                },
                                # "url": "https://help.strakertranslations.com/hc/en-us/articles/35943216049945-Instant-Document-Machine-Translation-AI-Translate-in-Straker-Translate-App-for-Slack",
                                "action_id": "verify_help",
                            },
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": _(
                                    ":bust_in_silhouette: Translating content from one language to another while preserving meaning and context"
                                ),
                            },
                            "accessory": {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "emoji": True,
                                    "text": _("Human Translation"),
                                },
                                "action_id": "human_help",
                            },
                        },
                    ]
                    if is_verify_enabled
                    else []
                ),
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
                        "text": _(":globe_with_meridians: View your connection."),
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
                        "text": _(":Seedling: Connect your account"),
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
                        "text": "🔴 " + _("Cancel your job"),
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
                        "url": "https://help.straker.ai/en/docs/workplace-apps#straker-translate-app-for-slack",
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
        is_ibm=False,
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
            user_details = f"<{domains.languagecloud}|{ray_connection.client.username}>"
            if is_ibm_enterprise(enterprise_id=enterprise_id):
                text = _("Your connected account is: {ray_connection.client.username}")
            else:
                text = _("Your connected account is: <{user_details}>")
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
                            "In order to use the Straker Translate features, please login. Click this button below;"
                        ),
                    },
                }
            )
            account_blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        (
                            (
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": _("Connect account"),
                                    },
                                    "style": "primary",
                                    "url": get_language_cloud_connect_url(
                                        user_id, team_id, enterprise_id, channel_id
                                    ),
                                    "action_id": "login",
                                }
                                if not is_ibm
                                else {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": _("Direct Login"),
                                    },
                                    "style": "primary",
                                    "action_id": "login_sso",
                                }
                            ),
                        )
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
            _(":no_entry_sign: Invalid command. Type `/straker help` for help.")
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
    def __init__(self, quote: Quote, is_ibm: bool) -> None:
        job_url = get_job_url(quote.uuid, quote.client_id)
        formatted_url = (
            "*<{job_url}|Straker Job Reference {quote.id}>*"
            if not is_ibm
            else "*Straker Job Reference {quote.id}*"
        )
        super().__init__(
            _("Pending Quote: Straker Job Reference {quote.id}"),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            formatted_url,
                        ),
                    },
                },
            ]
            + quote_message_block(quote, job_url, is_ibm),
        )


class SsoConnectionInfoMessage(SlackMessage):
    """The current Slack - LanguageCloud connection details."""

    def __init__(
        self,
        ray_connection: RayConnection,
        is_ibm=False,
    ) -> None:
        if ray_connection.client is not None:
            text = _("Your connected account is: *{ray_connection.client.username}*.")
        msg: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            },
        ]
        if ray_connection.client:
            msg.extend(
                [
                    {
                        "type": "actions",
                        "elements": (
                            [
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": _("Login to Verify"),
                                    },
                                    "style": "primary",
                                    # TODO: ray_connection.client could be None
                                    "url": encrpyt_slack_sso_token(
                                        ray_connection.client.username
                                    ),
                                    "action_id": "login",
                                }
                            ]
                        ),
                    }
                ]
                if not is_ibm
                else []
            )
        super().__init__(
            "Login Successfull",
            msg,
        )


# -----------------------------------------------------------------------------
# Ray event messages
# -----------------------------------------------------------------------------


class ClientSignupEventMessage(SlackMessage):
    def __init__(self, event: ClientSignupEvent) -> None:
        self.event = event
        super().__init__(
            "Thank you for signing :tada:",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "Thank you for signing {event.username} :tada:",
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
            _("A new user has signed up: {user_str}"),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "A new user has signed up:\n{user_str}",
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
                            "To approve this user please click the approve button below, or alternatively if you need to change anything, please log into Verify to edit their permissions."
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
                                "text": _("Log into Verify"),
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
            ":raised_hands: Your Verify groups have been approved by an Admin.",
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            ":raised_hands: Your Verify groups have been approved by an Admin:\n\n{groups_text}",
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
    def __init__(
        self, client_id: str, job_uuid: str, job_id: str, status: str, is_ibm: bool
    ) -> None:
        status_formatted = format_job_status(status)
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        "Your translation job *{job_id}* has changed status to: {status_formatted}",
                    ),
                },
            },
        ]
        if not is_ibm:
            blocks.append(
                job_link_block(job_uuid, client_id),
            )
        super().__init__(
            _(
                "Your translation job {job_id} has changed status to: {status_formatted}"
            ),
            blocks,
        )


class JobCompletedEventMessage(SlackMessage):
    def __init__(
        self,
        client_id: str,
        job_uuid: str,
        job_id: str,
        target_languages: list[str],
        is_ibm: bool,
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
        blocks: List[Dict[str, Any]] = [
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
        ]
        if not is_ibm:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "Please log into Verify below to access your completed files."
                        ),
                    },
                }
            )
            blocks.append(
                job_link_block(job_uuid, client_id),
            )
        super().__init__(
            _("Your files for {job_id} are ready to download :white_check_mark:"),
            blocks,
        )


class JobCancelledEventMessage(SlackMessage):
    def __init__(self, client_id: str, job_uuid: str, job_id: str) -> None:
        super().__init__(
            _("Your translation job {job_id} has been cancelled"),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "Your translation job *{job_id}* has been cancelled.",
                        ),
                    },
                },
            ],
        )


class JobQuoteAcceptedEventMessage(SlackMessage):
    def __init__(self, event: JobQuoteAcceptedEvent, is_ibm: bool) -> None:
        target_date = format_datetime_slack(event.target_date)
        id = event.id
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        ":clap: Quote Accepted for *{id}*. Your job will be completed before {target_date}."
                    ),
                },
            },
        ]
        if not is_ibm:
            blocks.append(job_link_block(event.uuid, event.client_id))
        super().__init__(
            _(
                "Quote Accepted for {id}. Your job will be completed before {target_date}.",
            ),
            blocks,
        )


class JobQuoteCancelledEventMessage(SlackMessage):
    def __init__(
        self, client_id: str, job_uuid: str, job_id: str, is_ibm: bool
    ) -> None:
        job_url = (
            f"<{get_job_url(job_uuid, client_id)}|{job_id}>" if not is_ibm else job_id
        )
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
    def __init__(self, event: JobQuoteCreatedEvent, is_ibm: bool) -> None:
        # job_url = f"<{get_job_url(event.uuid, event.client_id)}|{_('Straker Job Reference')} {event.id}>"
        job_url = get_job_url(event.uuid, event.client_id)
        reference = _("Straker Job Reference {event.id}")
        super().__init__(
            _(
                "Your quote is now ready :raised_hands: Straker Job Reference {event.id}"
            ),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            _(
                                "Your quote is now ready :raised_hands:\n**",
                            )
                            + f"<{job_url}|{reference}>**"
                            if not is_ibm
                            else reference if is_ibm else f"{reference}"
                        ),
                    },
                },
            ]
            + quote_message_block(event, job_url, is_ibm),
        )


class JobDelayMessage(SlackMessage):
    def __init__(self) -> None:
        message = _(
            "Our on-time AI prediction model has indicated that your job may be tracking behind schedule.\n\n"
        )
        message += _(
            "Our Project Managers have been notified and will be taking action to ensure that we still meet your due date. "
        )
        message += _(
            " If there is going to be a delay meeting your due dates, our Project Managers or your Account Manager will inform you. "
        )
        message += _(
            "This is only a prediction and should not be taken as an indication that your job is going to be late.\n\n"
        )
        message += _(
            "This status is updated in real time so can change if we predict it is tracking on time again."
        )

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


class VerifyCompleteMessage(SlackMessage):
    """Message to display a file which has been generated by the Verify Human verification service."""

    def __init__(self, job_title: str, lang_label: str) -> None:
        lang_label = _(lang_label)
        title = _(
            "Quality Evaluation Job '{job_title}' human verification complete. The file has been verified for language {lang_label}."
        )
        super().__init__(
            _("Verification Complete"),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": title,
                    },
                }
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


class AIHelperMessage(SlackMessage):
    def __init__(self) -> None:
        directmt_uri = "https://help.straker.ai/en/docs/direct-machine-translation-mt-in-straker-translate-app-for-slack"
        channelmt_uri = (
            "https://help.straker.ai/en/docs/how-to-use-channel-translations"
        )
        message = _(
            "Click me to learn Straker <{directmt_uri}|Direct MT> and <{channelmt_uri}|Channel Translations>."
        )
        bookEmoji = ":books:"
        super().__init__(
            _("{bookEmoji} Learn AI Channel Translations"),
            [{"type": "section", "text": {"type": "mrkdwn", "text": message}}],
        )


class VerifyHelperMessage(SlackMessage):
    def __init__(self) -> None:
        verify_uri = "https://help.straker.ai/en/docs/quality-evaluation"
        message = _(
            "Please upload your files to perform the Quality Evaluation in the message composer below. Click me to learn Straker <{verify_uri}|Quality Evaluation Help>."
        )
        bookEmoji = ":books:"
        super().__init__(
            _(f"{bookEmoji} Learn Quality Evaluation Help"),
            [{"type": "section", "text": {"type": "mrkdwn", "text": message}}],
        )


class HumanJobMessage(SlackMessage):
    def __init__(self) -> None:
        verify_uri = (
            "https://help.straker.ai/en/docs/human-verification-workflow-in-slack"
        )
        message = _(
            "Please upload your files to perform the Human Translation in the message composer below. Click me to learn Straker <{verify_uri}|Human Verification Help>."
        )
        bookEmoji = ":books:"
        super().__init__(
            _(f"{bookEmoji} Learn Human Translation Help"),
            [{"type": "section", "text": {"type": "mrkdwn", "text": message}}],
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
        self.scores = scores
        # Filter translations where target language does not equal source language
        self.translations = translations
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
            if (
                target_lang != self.source_language
                and langcodes.get(target_lang).language
                != langcodes.get(self.source_language).language
            ):
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

    def __init__(self, task_uuid: str, source_file_name: str, symlink: str) -> None:
        title = _(
            "We have *transcribed* your file *{source_file_name}* and SRT can be downloaded below."
        )
        # create message which contains the output_file
        super().__init__(
            title,
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": title.format(
                            source_file_name=source_file_name, symlink=symlink
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
                                "text": _("AI Translation"),
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
    def __init__(self, file_name: str) -> None:
        super().__init__(
            _("⏱️ Please wait a moment and we will transcribe your file *{file_name}*")
        )


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
                        "text": _("Click the *Cancel request* button below"),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Cancel request"),
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

    def __init__(self, jobdetail: dict[str, Any]) -> None:

        target_labels = [_(target.label) for target in jobdetail["targetlang"]]
        jobid = jobdetail["job_id"]
        jobstatus = _(format_job_status(jobdetail["status"]))
        sl = _(jobdetail["sourcelang"].label)
        tl = ", ".join(target_labels)
        super().__init__(
            "Cancel a translation job",
            [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": _("Cancel  {jobid}"),
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": _("*Status:*\n {jobstatus}")}
                    ],
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": _("*Source:*\n {sl}"),
                        },
                        {
                            "type": "mrkdwn",
                            "text": _("*Target:*\n {tl}"),
                        },
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _("Please confirm to cancel this job."),
                    },
                    "accessory": {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Cancel Job"),
                        },
                        "value": json.dumps(
                            {"job_id": jobdetail["job_id"], "job_action": "list"}
                        ),
                        "action_id": "cancel_job",
                    },
                },
            ],
        )


class AutoTranslateSettingsChangedMessage(TextMessage):
    """Message to send when the user changes their auto-translate settings."""

    def __init__(
        self, user_id: str, channel_id: str, langs: list[str], display_format: str
    ) -> None:
        langs_string = format_strings_display(
            [get_auto_translate_language_name(lang) for lang in langs], and_string="and"
        )
        display_format_string = (
            "thread replies" if display_format == "thread" else "messages"
        )
        user_mention = f"<@{user_id}>"
        super().__init__(
            _(
                "{user_mention} has changed the translation settings. The bot will respond to messages sent in <#{channel_id}> which will be translated into {langs_string} through "
                + display_format_string
                + " in real-time."
            )
        )


class AutoTranslateSettingsDisabledMessage(TextMessage):
    """Message to send when the user disable/enable their auto-translate settings."""

    def __init__(self, user_id: str, channel_id: str) -> None:
        user_mention = f"<@{user_id}>"
        super().__init__(
            _(
                "<#{channel_id}> Translation settings have been disabled by {user_mention}."
            )
        )


class RequiresMtTokenMessage(SlackMessage):
    def __init__(self, tokens: int, required_tokens: int) -> None:

        match (tokens, required_tokens):
            case (tokens, 1) if tokens <= 0:
                title = _(
                    "Your group account has no AI tokens. Please purchase tokens."
                )
            case (tokens, required_tokens) if tokens <= 0:
                title = _(
                    "Your group account has no AI tokens. This job requires *{required_tokens} AI tokens*. Please purchase tokens."
                )
            case _:
                title = _(
                    "You have *{tokens} AI tokens* on your account. This job requires *{required_tokens} AI tokens*. Please purchase tokens."
                )

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
                                "text": _("Purchase AI Tokens"),
                                "emoji": False,
                            },
                            "action_id": "link_1",
                            "url": f"{domains.verify}/settings?tab=usage",
                        },
                    ],
                },
            ],
        )


class RequiresMtTokenAdminMessage(SlackMessage):
    def __init__(self, tokens: int, required_tokens: int) -> None:

        match (tokens, required_tokens):
            case (tokens, 1) if tokens <= 0:
                title = _("Your group has no AI Tokens. Please purchase AI Tokens")
            case (tokens, required_tokens) if tokens <= 0:
                title = _(
                    "Your group has no AI Tokens. This job requires *{required_tokens} AI tokens*. Please contact your group admin to purchase more"
                )
            case _:
                title = _(
                    "You have *{tokens} AI tokens* on your group account. This job requires *{required_tokens} AI tokens*. Please contact your group admin to purchase more"
                )

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


class EvaluateSuccessMessage(SlackMessage):
    """Message verify consumer event response"""

    def __init__(
        self,
        job: dict[str, Any],
        all_langs: list[dict[str, str]],
        tokens: int,
        is_ibm_enterprise: bool,
    ) -> None:
        blocks = []
        if not is_ibm_enterprise:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _("You have used {tokens} AI tokens."),
                    },
                }
            )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _("AI quality evaluation of your translated files:"),
                },
            }
        )
        languages = job["target_languages"]
        source_files = job["source_files"]
        lang_blocks = []

        for file in source_files:
            source_lang_uuid = file["report"]["language_uuid"]
            source_lang = next(
                (lang for lang in all_langs if lang["uuid"] == source_lang_uuid), None
            )
            reports = file["report"]["evaluation_reports"]
            for lang in languages:
                for report in reports:
                    if lang["uuid"] == report["target_language"]:
                        lang["report"] = report
                for target_file in file["target_files"]:
                    if target_file["language_uuid"] == lang["uuid"]:
                        lang["target_file_uuid"] = target_file["target_file_uuid"]

            for lang in languages:
                lang_blocks.extend(
                    [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": job_summary_string(source_lang, lang, file),
                            },
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": _("Download AI Translation"),
                                    },
                                    "value": lang["target_file_uuid"],
                                    "action_id": "download_ai_translation_action",
                                },
                            ],
                        },
                    ]
                )

        blocks.extend(lang_blocks)
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Send to Human Verification"),
                        },
                        "style": "primary",
                        "value": job["uuid"],
                        "action_id": "verify_job_modal_open",
                    },
                ],
            },
        )
        super().__init__(_("Evaluation Result"), blocks)


class DocParseErrorMessage(SlackMessage):
    """Message verify consumer event response. Specific to faliure to parse file"""

    def __init__(self, ext: str, file_type: str) -> None:
        message = _(
            "Error parsing file. Please ensure file with {ext} is a valid {file_type}"
        )
        super().__init__(
            _("Verify the translation"),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": message,
                    },
                }
            ],
        )


class HumanJobQuoteMessage(SlackMessage):
    def __init__(
        self,
        job: dict[str, Any],
        costs: list[dict[str, Any]],
    ) -> None:
        accept_all = any(
            not target_file.get("human_job_status")
            for source_file in job["source_files"]
            for target_file in source_file.get("target_files", [])
        )
        blocks = []
        blocks = verify_quote_blocks(job, costs, False)
        if accept_all:
            blocks.insert(0, {"type": "divider"})
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Adjust Request"),
                            },
                            "value": job["uuid"],
                            "action_id": "quote_summary_modal_open",
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": _("Accept All"),
                            },
                            "style": "primary",
                            "value": job["uuid"],
                            "action_id": "quote_accept_all",
                        },
                    ],
                },
            )
        super().__init__(_("Adjust Request"), blocks)


class FileTooLargeMessage(SlackMessage):
    """Message to send when a file is too large to be processed."""

    def __init__(self, file_name: str, file_size: int) -> None:

        text = (
            f"*{file_name}* exceeds the current limit of 25MB "
            f"(~{file_size/1048576:.1f} MiB). "
            f"Please compress and re-upload according to the current limit."
        )

        super().__init__(
            _("File too large"),
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": text,
                    },
                },
            ],
        )
