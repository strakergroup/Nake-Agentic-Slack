"""Slack view templates (modals, home tab)."""

import json
from typing import Any, cast

from slack_bolt.context.async_context import AsyncBoltContext
from slack_sdk.errors import SlackApiError
from slack_sdk.models.blocks import (
    Block,
    InputBlock,
    MarkdownTextObject,
    Option,
    PlainTextObject,
    SectionBlock,
)
from slack_sdk.models.blocks.block_elements import (
    StaticMultiSelectElement,
)

from app.translate import _

from ...auth.connector import (
    RayConnection,
    get_channel_info,
    is_slack_team_admin,
)
from ...config import domains
from ...models import SlackGroupSettingsTranslation
from ...ray.settings import (
    get_auto_translate_language_name,
    get_full_group_translation_settings,
    get_pagination,
    update_channel_info,
)
from ...ray.utils import is_ibm_enterprise
from ...slack.utils import format_strings_display
from ..select_options import (
    filter_auto_translate_language_options,
    get_auto_translate_language_options,
    map_file_options,
    map_translation_display_format_option,
    translation_display_format_options,
)
from .blocks import (
    home_auth_blocks,
    verify_quote_blocks,
)


async def home_view(
    context: AsyncBoltContext, app_id: str, rayConnection: RayConnection | None, page=1
) -> dict[str, Any]:
    assert context.client

    message_url = f"slack://app?team={context['team_id']}&id={app_id}&tab=messages"
    barEmoji = ":bar_chart:"
    helpEmoji = ":question:"
    speechEmoji = ":speech_balloon:"
    is_straker_admin = (
        rayConnection
        and rayConnection.client
        and await is_slack_team_admin(rayConnection.client.id, context.enterprise_id)
    )
    translation_settings_enabled = not is_ibm_enterprise(context.enterprise_id) or (
        rayConnection and rayConnection.client and is_straker_admin
    )
    is_verify_enabled = (
        rayConnection.super_group[0].enable_verify_in_slack if rayConnection else False
    )
    verify_settings_block: list[dict[str, Any]] = []
    visible_translation_settings: list[
        tuple[SlackGroupSettingsTranslation, list[str], dict[str, str | bool]]
    ] = []
    questionEmoji = ":question:"
    rows_per_page = 5
    total_pages = await get_pagination(context, rows_per_page)
    translation_settings = await get_full_group_translation_settings(
        context, page, rows_per_page
    )
    if is_straker_admin:
        for setting, langs in translation_settings:
            # Use stored channel info if available
            if setting.channel_name is not None:
                visible_translation_settings.append(
                    (
                        setting,
                        langs,
                        {
                            "name": setting.channel_name,
                            "is_private": setting.is_private,
                        },
                    )
                )
            else:
                # Lazy update: fetch from Slack API and update database
                try:
                    info = await get_channel_info(
                        setting.channel_id,
                        context.client,
                        context.team_id or "",
                    )
                    # Update the database with channel info
                    await update_channel_info(
                        setting.id,
                        info.get("name"),
                        bool(info.get("is_private", False)),
                    )
                    visible_translation_settings.append((setting, langs, info))
                except SlackApiError:
                    visible_translation_settings.append((setting, langs, {}))
    else:
        visible_translation_settings = [
            (setting, langs, {}) for setting, langs in translation_settings
        ]
    footer_blocks = [
        {
            "type": "button",
            "text": {
                "type": "plain_text",
                "emoji": True,
                "text": _("{questionEmoji} Help Centre"),
            },
            "action_id": "link_2",
            "url": "https://help.straker.ai/en/docs/workplace-apps#straker-translate-app-for-slack",
        },
    ]
    # Domain needs to be updates to verify instead of languagecloud
    if not is_ibm_enterprise(context.enterprise_id):
        footer_blocks.append(
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "emoji": True,
                    "text": _("Visit Straker Verify"),
                },
                "action_id": "link_1",
                "url": domains.verify,
            },
        )
    translation_settings_blocks: list[dict[str, Any]] = []
    # if translation_settings_enabled:
    translation_settings_blocks = [
        {"type": "divider"},
        {
            "type": "header",
            "text": {"type": "plain_text", "text": _("Translate Channels")},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _(
                    "Transform your messages instantly so that everyone in your Slack channel can effortlessly understand and engage in conversations, regardless of their language preferences."
                ),
            },
        },
    ]
    if (
        isinstance(rayConnection, RayConnection)
        and rayConnection.client
        and translation_settings_enabled
    ):
        translation_settings_blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _(":speech_balloon: Translation settings"),
                        },
                        "value": json.dumps(
                            {
                                "team_id": context["team_id"],
                            }
                        ),
                        "action_id": "settings_auto_translate",
                    },
                ],
            },
        )
        if len(visible_translation_settings):
            translation_settings_blocks.extend(
                [
                    {"type": "divider"},
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": _("Current Translation Settings"),
                        },
                    },
                ]
            )
            for setting, langs, visible_info in visible_translation_settings:
                langs_string = format_strings_display(
                    [get_auto_translate_language_name(lang) for lang in langs],
                    and_string="and",
                )
                langs_string = langs_string.lower()
                # TODO refactor
                display_format_string = _(
                    "thread replies"
                    if setting.display_format == "thread"
                    else "messages"
                )
                message_trans = _(
                    "will be translated into {langs_string} through {display_format_string}"
                )
                error_msg = _("channel not found or bot not in channel")
                channel_name = f"({visible_info.get('name') if visible_info.get('name') else error_msg})"
                is_private = visible_info.get("is_private", False)
                should_display_channel_info = (is_straker_admin and is_private) or (
                    not visible_info.get("name") and is_straker_admin
                )
                translation_settings_blocks.extend(
                    [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"<#{setting.channel_id}>{channel_name if should_display_channel_info else ''} {message_trans}.",
                            },
                            # "accessory": {
                            #     "type": "button",
                            #     "text": {"type": "plain_text", "text": "Edit", "emoji": False},
                            #     "value": setting.channel_id,
                            #     "action_id": "settings_auto_translate",
                            # },
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": _("Edit"),
                                        "emoji": False,
                                    },
                                    "value": json.dumps(
                                        {
                                            "channel_id": setting.channel_id,
                                            "team_id": context["team_id"],
                                        }
                                    ),
                                    "action_id": "settings_auto_translate",
                                },
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": _("Disable"),
                                        "emoji": False,
                                    },
                                    "value": json.dumps(
                                        {
                                            "channel_id": setting.channel_id,
                                            "team_id": context["team_id"],
                                        }
                                    ),
                                    "action_id": "settings_auto_translate_disable",
                                },
                            ],
                        },
                    ]
                )
            actions = []
            if page > 1:
                actions.append(
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Previous"),
                            "emoji": True,
                        },
                        "value": json.dumps(
                            {
                                "team_id": context["team_id"],
                                "page": page - 1,
                            }
                        ),
                        "action_id": "home_load_previous",
                    },
                )
            if total_pages > 1 and page < total_pages:
                actions.append(
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Next"),
                            "emoji": True,
                        },
                        "value": json.dumps(
                            {
                                "team_id": context["team_id"],
                                "page": page + 1,
                            }
                        ),
                        "action_id": "home_load_next",
                    },
                )
            if len(actions):
                translation_settings_blocks.append(
                    {
                        "type": "actions",
                        "elements": actions,
                    }
                )
    # Note: translation_settings_enabled should be removed once we enable QE for everyone
    # if is_verify_enabled and translation_settings_enabled:
    #     verify_settings_block = [
    #         {
    #             "type": "section",
    #             "text": {
    #                 "type": "mrkdwn",
    #                 "text": _(
    #                     ":drum_with_drumsticks: Introducing a new option: AI Translate your content and receive translation quality scores, then opt for human verification if needed."
    #                 ),
    #             },
    #         },
    #         {
    #             "type": "actions",
    #             "elements": [
    #                 {
    #                     "type": "button",
    #                     "text": {
    #                         "type": "plain_text",
    #                         "emoji": True,
    #                         "text": _(":star2: Create New Project (QE)"),
    #                     },
    #                     "action_id": "verify_help",
    #                     "url": message_url,
    #                 },
    #             ],
    #         },
    #     ]
    return {
        "type": "home",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(":wave: Welcome to the Straker Translate!"),
                },
            },
            *home_auth_blocks(
                context["user_id"],
                context["team_id"],
                context.enterprise_id,
                context.get("channel_id"),  # TODO can be None, e.g. view_submission
                rayConnection,
            ),
            {"type": "divider"},
            {
                "type": "header",
                "text": {"type": "plain_text", "text": _("Get Started")},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        "Here are some things to get you started. Also make sure you check out our Help Centre and use our built in chatbot within our app to guide you through the translation process."
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    # *(
                    #     [
                    #         {
                    #             "type": "button",
                    #             "text": {
                    #                 "type": "plain_text",
                    #                 "emoji": True,
                    #                 "text": _(":zap: Create New Job"),
                    #             },
                    #             "action_id": "quote",
                    #             "url": message_url,
                    #         },
                    #     ]
                    #     if not is_verify_enabled
                    #     else []
                    # ),
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _(":sunny: Daily Summary"),
                        },
                        "action_id": "daily_summary",
                        "url": message_url,
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("{barEmoji} Insights"),
                        },
                        "action_id": "report_insights",
                        "url": message_url,
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": _("{helpEmoji} AI Translate Help"),
                        },
                        "action_id": "ai_translate_help",
                        "url": message_url,
                    },
                ],
            },
            *verify_settings_block,
            *translation_settings_blocks,
            {"type": "divider"},
            {
                "type": "header",
                "text": {"type": "plain_text", "text": _("Give us your feedback")},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        "Straker Community is a place for people who use Straker's users to provide feedback, and help each other get the most out of our platform. It's also a place for us to talk about the latest and greatest Verify and Enterprise features, provide updates, and engage with customers like you!"
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
                            "text": _("Learn More"),
                            "emoji": False,
                        },
                        "action_id": "link_0",
                        "url": "https://help.straker.ai/en/docs/straker-translate-functions",
                    },
                ],
            },
            {"type": "divider"},
            {"type": "actions", "elements": footer_blocks},
        ],
    }


def job_search_modal(
    client_name: str,
) -> dict[str, Any]:
    """The template for the modal to input TJ number and submit a search request

    Args:
        client_name (str): The user's LanguageCloud username.

    Returns:
        dict: The view dict.
    """

    return {
        "type": "modal",
        "callback_id": "job_search",
        "title": {"type": "plain_text", "text": _("Job Status", 23)[:24]},
        "submit": {"type": "plain_text", "text": _("Submit")},
        "close": {"type": "plain_text", "text": _("Close")},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _("You are searching for job(s) as `{client_name}`."),
                    "verbatim": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        "To search for multiple TJs, enter your TJ number, followed by a comma, then enter your next TJ reference, search for up to 10 TJs at once."
                    ),
                    "verbatim": True,
                },
            },
            {
                "type": "input",
                "block_id": "reference",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "reference",
                    "placeholder": {
                        "type": "plain_text",
                        "text": _("Your job reference"),
                        "emoji": True,
                    },
                    "max_length": 110,
                },
                "label": {
                    "type": "plain_text",
                    "text": _("Your job reference"),
                    "emoji": True,
                },
            },
        ],
    }


def human_job_modal(
    channel_id: str,
    file_info: list[dict[str, Any]],
    is_ibm_enterprise: bool,
    job_type: str = "human",
):
    """The template for the modal to submit a file to verify quality evaluation or human translation."""
    file_options, initial_options = map_file_options(file_info)
    files_block_element = {
        "type": "multi_static_select",
        "action_id": "files",
        "placeholder": {
            "type": "plain_text",
            "text": _("Select file(s)"),
            "emoji": True,
        },
        "options": file_options,
    }
    if initial_options:
        files_block_element["initial_options"] = initial_options

    # Determine title, submit text, and description based on job type
    if job_type == "human":
        title = _("Human Translation", 23)[:24]
        submit_text = _("Request Quote", 23)[:24]
        description = _("Files and languages to be sent for *human translation*.")
        callback_id = "evaluate_job_human"
        close_text = _("Cancel")
        files_label = _("Files to be translated")
        include_job_notes = True
    else:
        title = _("Quality Evaluation", 23)[:24]
        submit_text = _("Submit", 23)[:24]
        description = _(
            "AI translate your content and receive translation quality scores, then opt for human verification if needed."
        )
        callback_id = "evaluate_job"
        close_text = _("Close")
        files_label = _("File(s) to evaluate")
        include_job_notes = False

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": description,
            },
        },
        # separator
        {"type": "divider"},
    ]
    # Add files block
    blocks.append(
        {
            "type": "input",
            "block_id": "files",
            "element": files_block_element,
            "label": {
                "type": "plain_text",
                "text": files_label,
                "emoji": True,
            },
        }
    )

    if not is_ibm_enterprise:
        blocks.append(
            {
                "type": "input",
                "block_id": "reference",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "reference",
                    "placeholder": {
                        "type": "plain_text",
                        "text": _("Project Name"),
                        "emoji": True,
                    },
                    "min_length": 4,
                    "max_length": 110,
                },
                "label": {
                    "type": "plain_text",
                    "text": _("Project Name"),
                    "emoji": True,
                },
            },
        )

    # Add target languages block (shared for both job types)
    target_langs_block = {
        "type": "input",
        "block_id": "target_langs",
        "element": {
            "type": "multi_external_select",
            "placeholder": {
                "type": "plain_text",
                "text": _("Select languages"),
                "emoji": True,
            },
            "action_id": "language_options_uuid",
            "min_query_length": 0,
        },
        "label": {
            "type": "plain_text",
            "text": _("Translate to"),
            "emoji": True,
        },
    }

    blocks.append(target_langs_block)

    # Add job notes for human job
    if include_job_notes:
        blocks.append(
            {
                "type": "input",
                "block_id": "job_notes",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "job_notes",
                    "placeholder": {
                        "type": "plain_text",
                        # Slack will throw an error if this is 0 characters
                        "text": " ",
                        "emoji": True,
                    },
                    "multiline": True,
                    "max_length": 255,
                },
                "label": {
                    "type": "plain_text",
                    "text": _("Job Notes"),
                    "emoji": True,
                },
                "optional": True,
            }
        )

    return {
        "type": "modal",
        "callback_id": callback_id,
        "title": {"type": "plain_text", "text": title},
        "submit": {"type": "plain_text", "text": submit_text},
        "private_metadata": channel_id,
        "close": {"type": "plain_text", "text": close_text},
        "blocks": blocks,
    }


def sso_form_modal() -> dict[str, Any]:
    """The template for the modal to submit a new translation job. The user can
    select the files they want to translate and enter the job details, e.g.
    category, source and target languages.

    Returns:
        dict: The view dict.
    """
    return {
        "title": {"type": "plain_text", "text": _("Direct Login", 23)[:24]},
        "submit": {"type": "plain_text", "text": _("Submit")},
        "blocks": [
            {
                "type": "input",
                "block_id": "email",
                "element": {
                    "type": "email_text_input",
                    "action_id": "email",
                    "placeholder": {"type": "plain_text", "text": _("Email")},
                },
                "label": {"type": "plain_text", "text": _("Email")},
                "optional": False,
            },
            {
                "type": "input",
                "block_id": "firstName",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "firstName",
                    "placeholder": {"type": "plain_text", "text": _("First Name")},
                    "min_length": 3,
                    "max_length": 50,
                },
                "label": {"type": "plain_text", "text": _("First Name")},
                "optional": False,
            },
            {
                "type": "input",
                "block_id": "lastName",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "lastName",
                    "placeholder": {"type": "plain_text", "text": _("Last Name")},
                    "min_length": 3,
                    "max_length": 50,
                },
                "label": {"type": "plain_text", "text": _("Last Name")},
                "optional": False,
            },
        ],
        "type": "modal",
        "callback_id": "login_sso",
    }


def cancel_job_modal(client_name: str) -> dict[str, Any]:
    """The template for the modal to cancel TJ by insert number and submit a search request

    Args:
        client_name (str): The user's LanguageCloud username.

    Returns:
        dict: The view dict.
    """

    return {
        "type": "modal",
        "callback_id": "cancel_job",
        "title": {"type": "plain_text", "text": _("Cancel Job", 23)[:24]},
        "submit": {"type": "plain_text", "text": _("Submit")},
        "close": {"type": "plain_text", "text": _("Close")},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _("You are cancelling a job as `{client_name}`."),
                    "verbatim": True,
                },
            },
            {
                "type": "input",
                "block_id": "reference",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "reference",
                    "placeholder": {
                        "type": "plain_text",
                        "text": _("Your TJ number"),
                        "emoji": True,
                    },
                    "max_length": 100,
                },
                "label": {
                    "type": "plain_text",
                    "text": _("Your job TJ number"),
                    "emoji": True,
                },
            },
        ],
    }


def translation_settings_view(
    initial_channels: list[str] | None = None,
    initial_langs: list[str] | None = None,
    display_format: SlackGroupSettingsTranslation.DisplayFormatType = "thread",
    team_id: str = "",
) -> dict[str, Any]:
    # TODO: Detect message max length (5000)
    # TODO: Detect message formatting, emojis
    # TODO: 429 rate limiting
    language_options = get_auto_translate_language_options()
    display_format_options = translation_display_format_options()
    # TODO Rethink this, accept single value?
    initial_channels = initial_channels or []
    initial_lang_options = (
        filter_auto_translate_language_options(initial_langs) if initial_langs else []
    )
    initial_display_format_option = map_translation_display_format_option(
        cast(SlackGroupSettingsTranslation.DisplayFormatType, display_format)
    )
    return {
        "type": "modal",
        "callback_id": "settings_auto_translate",
        "private_metadata": team_id,
        "title": {"type": "plain_text", "text": _("Translation Settings", 23)[:24]},
        "submit": {"type": "plain_text", "text": _("Create")},
        "close": {"type": "plain_text", "text": _("Close")},
        "blocks": [
            {
                "type": "input",
                "block_id": "channels",
                "element": {
                    "type": "multi_conversations_select",
                    "action_id": "channels",
                    "placeholder": {
                        "type": "plain_text",
                        "text": _("Select channels or DMs"),
                    },
                    # TODO default_to_current_conversation? Consider when im
                    **(
                        {"initial_conversations": initial_channels}
                        if initial_channels
                        else {"default_to_current_conversation": True}
                    ),
                    "filter": {
                        "include": ["public", "private", "mpim"],
                        "exclude_bot_users": True,
                    },
                },
                "label": {
                    "type": "plain_text",
                    "text": _("Channel or DM"),
                    "emoji": False,
                },
                "hint": {
                    "type": "plain_text",
                    "text": _(
                        "Straker Translate must be integrated as an app in the selected channel or DM"
                    ),
                },
                "optional": False,
            },
            {
                "type": "input",
                "block_id": "languages",
                "element": {
                    "type": "multi_static_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": _("Select languages"),
                    },
                    "options": language_options,
                    **(
                        {"initial_options": initial_lang_options}
                        if initial_lang_options
                        else {}
                    ),
                    "action_id": "languages",
                    "max_selected_items": 10,
                },
                "label": {"type": "plain_text", "text": _("Language"), "emoji": False},
                "hint": {
                    "type": "plain_text",
                    "text": _("Automatically translate messages into these languages"),
                },
                "optional": True,
            },
            {
                "type": "input",
                "block_id": "display_format",
                "element": {
                    "type": "static_select",
                    "options": display_format_options,
                    "initial_option": initial_display_format_option,
                    "action_id": "display_format",
                },
                "label": {
                    "type": "plain_text",
                    "text": _("Display Format"),
                    "emoji": False,
                },
                "hint": {
                    "type": "plain_text",
                    "text": _("How would you like to see the translated messages?"),
                },
                "optional": False,
            },
        ],
    }


def translation_settings_view_error(message: str) -> dict[str, Any]:
    return {
        "type": "modal",
        "title": {
            "type": "plain_text",
            "text": _("Translation Settings", 23)[:24],
        },
        "close": {"type": "plain_text", "text": _("Close")},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "plain_text", "text": message},
            }
        ],
    }


def verify_job_modal(
    job: dict[str, Any],
    costs: list[dict[str, Any]],
    timestamp: str,
) -> dict[str, Any]:
    """Generate modal for job verification with total cost calculation."""
    blocks = verify_quote_blocks(job, costs)
    return {
        "type": "modal",
        "callback_id": "verify_job",
        "title": {"type": "plain_text", "text": _("Adjust Request", 23)[:24]},
        "submit": {"type": "plain_text", "text": _("Submit")},
        "close": {"type": "plain_text", "text": _("Cancel")},
        "private_metadata": json.dumps(
            {
                "job_uuid": job["uuid"],
                "timestamp": timestamp,
            }
        ),
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        "Please deselect any unneeded files or target languages before submitting for human verification. Submitted orders cannot be cancelled. "
                    ),
                },
            },
            {
                "type": "divider",
            },
            *blocks,
        ],
    }


def verify_quote_summary_modal(
    job: dict[str, Any],
    costs: list[dict[str, Any]],
    timestamp: str,
) -> dict[str, Any]:
    blocks = verify_quote_blocks(job, costs)

    return {
        "type": "modal",
        "callback_id": "verify_job",
        "title": {"type": "plain_text", "text": _("Adjust Request", 23)[:24]},
        "submit": {"type": "plain_text", "text": _("Submit")},
        "close": {"type": "plain_text", "text": _("Cancel")},
        "private_metadata": json.dumps(
            {
                "job_uuid": job["uuid"],
                "timestamp": timestamp,
            }
        ),
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        "Please deselect any unneeded files or target languages before submitting for human translation. Submitted orders cannot be cancelled."
                    ),
                },
            },
            {
                "type": "divider",
            },
            *blocks,
        ],
    }


def calculate_total_cost(
    selected_languages: list[dict[str, Any]], costs: list[dict[str, Any]]
) -> float:
    """Calculate the total cost based on selected languages."""
    total_cost = 0.0
    for lang in selected_languages:
        for cost_item in costs:
            if cost_item["language_uuid"] == lang["uuid"]:
                total_cost += cost_item["service_list"][0]["estimated_cost"]
                break
    return total_cost


def document_mt_job_modal(
    channel_id: str,
    initial_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    language_options = get_auto_translate_language_options()
    file_options, initial_options = (
        map_file_options(initial_files) if initial_files else ([], [])
    )
    files_block_element = {
        "type": "multi_static_select",
        "action_id": "files",
        "placeholder": {
            "type": "plain_text",
            "text": _("Select file(s)"),
            "emoji": True,
        },
        "options": file_options,
    }
    if initial_options:
        files_block_element["initial_options"] = initial_options
    return {
        "type": "modal",
        "callback_id": "document_mt_job",
        "title": {"type": "plain_text", "text": _("Document MT Job", 23)[:24]},
        "submit": {"type": "plain_text", "text": _("Submit")},
        "close": {"type": "plain_text", "text": _("Close")},
        "private_metadata": channel_id,
        "blocks": [
            {
                "type": "section",
                "text": {"type": "plain_text", "text": _("Document MT Job", 23)[:24]},
            },
            {
                "type": "input",
                "block_id": "target_langs",
                "element": {
                    "type": "multi_static_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": _("Select languages"),
                        "emoji": True,
                    },
                    "options": language_options,
                    "action_id": "language_mt_options",
                },
                "label": {
                    "type": "plain_text",
                    "text": _("Translate to"),
                    "emoji": True,
                },
                "hint": {
                    "type": "plain_text",
                    "text": _(
                        "Which language(s) do you want the file(s) to be translated to?"
                    ),
                },
            },
            {
                "type": "input",
                "block_id": "files",
                "element": files_block_element,
                "label": {
                    "type": "plain_text",
                    "text": _("File(s) to translate"),
                    "emoji": True,
                },
            },
        ],
    }


def loading_modal() -> dict[str, Any]:
    """Creates a simple loading modal template.

    Returns:
        dict: The view dict for a loading modal.
    """
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": _("Processing..."), "emoji": True},
        "close": {"type": "plain_text", "text": _("Cancel"), "emoji": True},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        ":hourglass: Please wait while we process your request..."
                    ),
                    "verbatim": True,
                },
            }
        ],
    }


def srt_translate_modal(task_uuid: str, channel_id: str) -> dict[str, Any]:
    """Modal to allow user to select language and submit for machine translation.

    Args:
        task_uuid: The UUID of the transcription task.
        channel_id: The channel ID where the translation should be posted.

    Returns:
        dict: The view dict for the SRT translate modal.
    """
    language_options_raw = get_auto_translate_language_options()

    # Convert raw options to SDK Option objects
    # Truncate text to 75 chars (Slack limit for option text)
    language_options = [
        Option(
            text=PlainTextObject(text=opt["text"]["text"][:75], emoji=False),
            value=opt["value"],
        )
        for opt in language_options_raw
    ]

    # Create multi-select element
    multi_select = StaticMultiSelectElement(
        placeholder=PlainTextObject(text=_("Select language"), emoji=False),
        options=language_options,
        action_id="language_mt_options",
        max_selected_items=10,
    )

    # Create input block
    input_block = InputBlock(
        block_id="target_langs",
        label=PlainTextObject(text=_("Select languages"), emoji=False),
        element=multi_select,
    )

    # Create section block
    section_block = SectionBlock(
        text=MarkdownTextObject(
            text=_("Please select the target language(s) for translation")
        )
    )

    # Convert blocks to dictionaries
    blocks = [section_block.to_dict(), input_block.to_dict()]

    # Store both task_uuid and channel_id in private_metadata (same pattern as document_mt_job)
    private_metadata = f"{task_uuid}|{channel_id}"

    return {
        "type": "modal",
        "callback_id": "srt_translate",
        "title": PlainTextObject(text=_("Select Languages", 23)[:24]).to_dict(),
        "submit": PlainTextObject(text=_("Submit")).to_dict(),
        "close": PlainTextObject(text=_("Close")).to_dict(),
        "private_metadata": private_metadata,
        "blocks": blocks,
    }


def video_transcribe_translate_modal(
    channel_id: str,
    files: list[dict],  # [{file_id, file_name, duration_ms}, ...]
    thread_ts: str | None = None,
) -> dict[str, Any]:
    """Modal for video transcription with translation - requires language selection.

    Args:
        channel_id: The Slack channel ID.
        files: List of file dicts with file_id, file_name, duration_ms.
        thread_ts: Optional thread timestamp.

    Returns:
        dict: The view dict for the transcribe & translate modal.
    """
    # Build blocks using SDK
    blocks: list[Block] = []

    # Description section
    blocks.append(
        SectionBlock(
            text=MarkdownTextObject(
                text=_(
                    "To transcribe your file(s) and get an AI Translation, select your file(s) and choose the desired target language(s)."
                )
            )
        )
    )

    # File display section - show selected files (read-only display)
    # Build options for all files
    file_options = [
        Option(
            text=PlainTextObject(text=f["file_name"][:75], emoji=False),
            value=f["file_id"],
        )
        for f in files
    ]
    blocks.append(
        InputBlock(
            block_id="selected_file",
            label=PlainTextObject(text=_("Select your files to translate")),
            element=StaticMultiSelectElement(
                action_id="file_display",
                placeholder=PlainTextObject(text=_("Selected files")),
                options=file_options,
                initial_options=file_options,
            ),
            optional=False,
        )
    )

    # Get language options with proper ISO codes
    language_options_raw = get_auto_translate_language_options()
    # Truncate text to 75 chars (Slack limit for option text)
    language_options = [
        Option(
            text=PlainTextObject(text=opt["text"]["text"][:75], emoji=False),
            value=opt["value"],
        )
        for opt in language_options_raw
    ]

    # Target languages selection
    blocks.append(
        InputBlock(
            block_id="target_languages",
            label=PlainTextObject(text=_("Translate to")),
            element=StaticMultiSelectElement(
                action_id="language_mt_options",
                placeholder=PlainTextObject(text=_("Select languages")),
                options=language_options,
            ),
        )
    )

    return {
        "type": "modal",
        "callback_id": "video_transcribe_translate_submit",
        "private_metadata": json.dumps(
            {
                "channel_id": channel_id,
                "files": files,
                "thread_ts": thread_ts,
                "pipeline_type": "transcription_translation",
            }
        ),
        "title": {"type": "plain_text", "text": _("Transcribe+AI Translate")[:24]},
        "submit": {"type": "plain_text", "text": _("Submit")},
        "close": {"type": "plain_text", "text": _("Cancel")},
        "blocks": [block.to_dict() for block in blocks],
    }


def video_embed_subtitles_modal(
    channel_id: str,
    files: list[dict],  # [{file_id, file_name, duration_ms}, ...]
    thread_ts: str | None = None,
) -> dict[str, Any]:
    """Modal for video transcription with translation and subtitle embedding - requires language selection.

    Args:
        channel_id: The Slack channel ID.
        files: List of file dicts with file_id, file_name, duration_ms.
        thread_ts: Optional thread timestamp.

    Returns:
        dict: The view dict for the embed subtitles modal.
    """
    # Build blocks using SDK
    blocks: list[Block] = []

    # Description section
    blocks.append(
        SectionBlock(
            text=MarkdownTextObject(
                text=_(
                    "To automatically transcribe, translate, and embed subtitles, please select your file(s) and choose the desired target language(s)."
                )
            )
        )
    )

    # File display section - show selected files (read-only display)
    # Build options for all files
    file_options = [
        Option(
            text=PlainTextObject(text=f["file_name"][:75], emoji=False),
            value=f["file_id"],
        )
        for f in files
    ]
    blocks.append(
        InputBlock(
            block_id="selected_file",
            label=PlainTextObject(text=_("Select your files to process")),
            element=StaticMultiSelectElement(
                action_id="file_display",
                placeholder=PlainTextObject(text=_("Selected files")),
                options=file_options,
                initial_options=file_options,
            ),
            optional=False,
        )
    )

    # Get language options with proper ISO codes
    language_options_raw = get_auto_translate_language_options()
    # Truncate text to 75 chars (Slack limit for option text)
    language_options = [
        Option(
            text=PlainTextObject(text=opt["text"]["text"][:75], emoji=False),
            value=opt["value"],
        )
        for opt in language_options_raw
    ]

    # Target languages selection
    blocks.append(
        InputBlock(
            block_id="target_languages",
            label=PlainTextObject(text=_("Translate to")),
            element=StaticMultiSelectElement(
                action_id="language_mt_options",
                placeholder=PlainTextObject(text=_("Select languages")),
                options=language_options,
            ),
        )
    )

    return {
        "type": "modal",
        "callback_id": "video_embed_subtitles_submit",
        "private_metadata": json.dumps(
            {
                "channel_id": channel_id,
                "files": files,
                "thread_ts": thread_ts,
                "pipeline_type": "transcription_translation_embed",
            }
        ),
        "title": {"type": "plain_text", "text": _("Embed Subtitles")[:24]},
        "submit": {"type": "plain_text", "text": _("Submit")},
        "close": {"type": "plain_text", "text": _("Cancel")},
        "blocks": [block.to_dict() for block in blocks],
    }
