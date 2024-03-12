"""Slack view templates (modals, home tab)."""

from typing import Any
from slack_bolt.context.async_context import AsyncBoltContext

from .blocks import home_auth_blocks
from ..select_options import (
    map_file_options,
    get_auto_translate_language_options,
    filter_auto_translate_language_options,
)
from ...auth.connector import RayConnection
from ...ray.utils import is_min_langugagecloud_plan
from ...config import config, domains, Environment


def home_view(
    context: AsyncBoltContext, app_id: str, rayConnection: RayConnection | None
) -> dict[str, Any]:
    message_url = f"slack://app?team={context['team_id']}&id={app_id}&tab=messages"
    # Hide auto-translation settings in Production until scopes are approved.
    auto_translate_blocks: list[dict[str, Any]] = [
        {"type": "divider"},
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Translate Channels"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Transform your messages instantly so that everyone in your Slack channel can effortlessly understand and engage in conversations, regardless of their language preferences.",
            },
        },
        (
            (
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "emoji": True,
                                "text": ":speech_balloon: Translation Settings",
                            },
                            "action_id": "settings_auto_translate",
                        },
                    ],
                }
                if is_min_langugagecloud_plan(
                    rayConnection.client.planname, "Essentials"
                )
                else {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "_This feature is only available on an Essentials plan or higher_",
                    },
                }
            )
            if rayConnection and rayConnection.client
            else {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "_Connect your Straker LanguageCloud account to enable this feature_",
                },
            }
        ),
    ]
    return {
        "type": "home",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Welcome to the Straker LanguageCloud App!",
                },
            },
            *home_auth_blocks(
                context["user_id"],
                context["team_id"],
                context.get("enterprise_id"),
                context["channel_id"],
                rayConnection,
            ),
            {"type": "divider"},
            {"type": "header", "text": {"type": "plain_text", "text": "Get started"}},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Here are some things to get you started. Also make sure you check out our Help Centre and use our built in chatbot within our app to guide you through the translation process.",
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
                            "text": "⚡️ Create New Job",
                        },
                        "style": "primary",
                        "action_id": "quote",
                        "url": message_url,
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "☀️ Daily Summary",
                        },
                        "action_id": "daily_summary",
                        "url": message_url,
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "📊 Reports/Insights",
                        },
                        "action_id": "report_insights",
                        "url": message_url,
                    },
                ],
            },
            *(
                auto_translate_blocks
                if config.environment != Environment.production
                else []
            ),
            {"type": "divider"},
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Give us your feedback"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Straker Community is a place for people who use Straker's users to provide feedback, and help each other get the most out of our platform. It's also a place for us to talk about the latest and greatest LanguageCloud and Enterprise features, provide updates, and engage with customers like you!",
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Learn More", "emoji": True},
                    "action_id": "link_0",
                    "url": "https://help.strakertranslations.com/hc/en-us/articles/22925760887833-Slack-app-functions",
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
                            "text": "🌐 Visit Straker LanguageCloud",
                        },
                        "action_id": "link_1",
                        "url": domains.languagecloud,
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": "❓Help Centre",
                        },
                        "action_id": "link_2",
                        "url": "https://help.strakertranslations.com/hc/en-us/categories/10020714644633-Apps",
                    },
                ],
            },
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
        "title": {"type": "plain_text", "text": "Job Status"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"You are searching for job(s) as `{client_name}`.",
                    "verbatim": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "To search for multiple TJs, enter your TJ number, followed by a comma, then enter your next TJ reference, search for up to 10 TJs at once.",
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
                        "text": "Your job reference",
                        "emoji": True,
                    },
                    "max_length": 110,
                },
                "label": {
                    "type": "plain_text",
                    "text": "Your job reference",
                    "emoji": True,
                },
            },
        ],
    }


def new_job_modal(
    client_name: str,
    channel_id: str,
    file_options: list[dict[str, Any]] | None = None,
    initial_files: list[dict[str, Any]] | None = None,
    max_selected_files: int = 10,
) -> dict[str, Any]:
    """The template for the modal to submit a new translation job. The user can
    select the files they want to translate and enter the job details, e.g.
    category, source and target languages.

    Args:
        client_name (str): The user's LanguageCloud username.
        file_options (list[dict] | None, optional): A list of file objects to set as available
            options for the "Files to translate" select input. Defaults to None.
        initial_files (list[dict] | None, optional): A list of file objects to initally select.
            Defaults to None.
        max_selected_files (int, optional): The maximum number of files to translate. Defaults to 10.

    Returns:
        dict: The view dict.
    """

    file_options = file_options or []
    initial_files = (
        map_file_options(initial_files[:max_selected_files]) if initial_files else []
    )
    # Add the initial files to the file options if they are not there already.
    for file in initial_files:
        if not any(file["value"] == opt["value"] for opt in file_options):
            file_options.insert(0, file)
    file_options = file_options[:100]

    if file_options:
        files_block_element = {
            "type": "multi_static_select",
            "placeholder": {
                "type": "plain_text",
                "text": "Select file(s)",
                "emoji": True,
            },
            "options": file_options,
            "action_id": f"file_options_{channel_id}",
            "max_selected_items": max_selected_files,
        }
    else:
        # Use external select if no files are given because options cannot be empty.
        files_block_element = {
            "type": "multi_external_select",
            "placeholder": {
                "type": "plain_text",
                "text": "Select file(s)",
                "emoji": True,
            },
            "action_id": f"file_options_{channel_id}",
            "max_selected_items": max_selected_files,
            "min_query_length": 0,
        }
    if initial_files:
        files_block_element["initial_options"] = initial_files

    return {
        "type": "modal",
        "callback_id": "new_job",
        "title": {"type": "plain_text", "text": "New Translation Job"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"You are submitting a new job as `{client_name}`.",
                    "verbatim": True,
                },
            },
            {
                "type": "input",
                "block_id": "files",
                "element": files_block_element,
                "label": {
                    "type": "plain_text",
                    "text": "File(s) to translate",
                    "emoji": True,
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
                        "text": "Your job reference",
                        "emoji": True,
                    },
                    "max_length": 100,
                },
                "label": {
                    "type": "plain_text",
                    "text": "Your reference",
                    "emoji": True,
                },
                "optional": True,
            },
            {
                "type": "input",
                "block_id": "source_lang",
                "element": {
                    "type": "external_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select a source language",
                        "emoji": True,
                    },
                    "action_id": "language_options",
                    "min_query_length": 0,
                },
                "label": {
                    "type": "plain_text",
                    "text": "Source language",
                    "emoji": True,
                },
                "hint": {
                    "type": "plain_text",
                    "text": "What is the original language of the file(s)? Type to show more languages.",
                },
            },
            {
                "type": "input",
                "block_id": "target_langs",
                "element": {
                    "type": "multi_external_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select target language(s)",
                        "emoji": True,
                    },
                    "action_id": "language_options",
                    "min_query_length": 0,
                },
                "label": {
                    "type": "plain_text",
                    "text": "Target language(s)",
                    "emoji": True,
                },
                "hint": {
                    "type": "plain_text",
                    "text": "Which language(s) do you want the file(s) to be translated to?",
                },
            },
            {
                "type": "input",
                "block_id": "group",
                "element": {
                    "type": "external_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select group",
                        "emoji": True,
                    },
                    "action_id": "group_options",
                    "min_query_length": 0,
                },
                "label": {
                    "type": "plain_text",
                    "text": "Group",
                    "emoji": True,
                },
                "hint": {
                    "type": "plain_text",
                    "text": "Which group do you want to submit job for?",
                },
                "optional": True,
            },
            # {
            #     "type": "input",
            #     "block_id": "target_date",
            #     "element": {
            #         "type": "datepicker",
            #         "placeholder": {
            #             "type": "plain_text",
            #             "text": "Select a date",
            #             "emoji": True,
            #         },
            #         "action_id": "target_date",
            #     },
            #     "label": {"type": "plain_text", "text": "Target date", "emoji": True},
            # },
            {
                "type": "input",
                "block_id": "service",
                "element": {
                    "type": "static_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select a service",
                        "emoji": True,
                    },
                    "options": [
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Translation",
                                "emoji": False,
                            },
                            "value": "Translation",
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Translation + Edit",
                                "emoji": False,
                            },
                            "value": "Translation + Edit",
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Machine Translation",
                                "emoji": False,
                            },
                            "value": "Machine Translation",
                        },
                    ],
                    "initial_option": {
                        "text": {
                            "type": "plain_text",
                            "text": "Translation",
                            "emoji": False,
                        },
                        "value": "Translation",
                    },
                    "action_id": "service",
                },
                "label": {"type": "plain_text", "text": "Service", "emoji": True},
            },
            {
                "type": "input",
                "block_id": "timeframe",
                "element": {
                    "type": "static_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select a timeframe",
                        "emoji": True,
                    },
                    "options": [
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Within 12 hours",
                                "emoji": False,
                            },
                            "value": "1",
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Within 24 hours",
                                "emoji": False,
                            },
                            "value": "2",
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Within 36 hours",
                                "emoji": False,
                            },
                            "value": "3",
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Within 48 hours",
                                "emoji": False,
                            },
                            "value": "4",
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Within 3 days",
                                "emoji": False,
                            },
                            "value": "5",
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Within 5 days",
                                "emoji": False,
                            },
                            "value": "6",
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Within 10 days",
                                "emoji": False,
                            },
                            "value": "7",
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Within 15 days",
                                "emoji": False,
                            },
                            "value": "8",
                        },
                    ],
                    "initial_option": {
                        "text": {
                            "type": "plain_text",
                            "text": "Within 3 days",
                            "emoji": False,
                        },
                        "value": "5",
                    },
                    "action_id": "timeframe",
                },
                "label": {"type": "plain_text", "text": "Timeframe", "emoji": True},
            },
            {
                "type": "input",
                "block_id": "validation",
                "element": {
                    "type": "checkboxes",
                    "options": [
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Yes",
                                "emoji": True,
                            },
                            "value": "1",
                        },
                    ],
                    "action_id": "validation",
                },
                "label": {"type": "plain_text", "text": "Validation", "emoji": True},
                "optional": True,
            },
            {
                "type": "input",
                "block_id": "notes",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "notes",
                    "multiline": True,
                    "max_length": 250,
                },
                "label": {
                    "type": "plain_text",
                    "text": "Notes",
                    "emoji": True,
                },
                "optional": True,
            },
            {
                "type": "input",
                "block_id": "translation_notes",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "translation_notes",
                    "multiline": True,
                    "max_length": 250,
                },
                "label": {
                    "type": "plain_text",
                    "text": "Job Notes",
                    "emoji": True,
                },
                "optional": True,
            },
            # TODO: job category?
            # {
            #     "type": "input",
            #     "block_id": "category",
            #     "element": {
            #         "type": "static_select",
            #         "placeholder": {
            #             "type": "plain_text",
            #             "text": "Select a category",
            #             "emoji": True,
            #         },
            #         "option_groups": [
            #             {
            #                 "label": {
            #                     "type": "plain_text",
            #                     "text": "Advertising/Marketing",
            #                 },
            #                 "options": [
            #                     {
            #                         "text": {
            #                             "type": "plain_text",
            #                             "text": "Art/Literary",
            #                             "emoji": False,
            #                         },
            #                         "value": "art_literary",
            #                     },
            #                     {
            #                         "text": {
            #                             "type": "plain_text",
            #                             "text": "Cosmetics",
            #                             "emoji": False,
            #                         },
            #                         "value": "cosmetics",
            #                     },
            #                     {
            #                         "text": {
            #                             "type": "plain_text",
            #                             "text": "Cultural",
            #                             "emoji": False,
            #                         },
            #                         "value": "cultural",
            #                     },
            #                 ],
            #             },
            #             {
            #                 "label": {
            #                     "type": "plain_text",
            #                     "text": "Finance, Business & HR",
            #                 },
            #                 "options": [
            #                     {
            #                         "text": {
            #                             "type": "plain_text",
            #                             "text": "Accounting",
            #                             "emoji": False,
            #                         },
            #                         "value": "accounting",
            #                     },
            #                     {
            #                         "text": {
            #                             "type": "plain_text",
            #                             "text": "Banking",
            #                             "emoji": False,
            #                         },
            #                         "value": "banking",
            #                     },
            #                     {
            #                         "text": {
            #                             "type": "plain_text",
            #                             "text": "Business",
            #                             "emoji": False,
            #                         },
            #                         "value": "business",
            #                     },
            #                 ],
            #             },
            #         ],
            #         "action_id": "category",
            #     },
            #     "label": {"type": "plain_text", "text": "Category", "emoji": True},
            # },
        ],
    }


def sso_form_modal() -> dict[str, Any]:
    """The template for the modal to submit a new translation job. The user can
    select the files they want to translate and enter the job details, e.g.
    category, source and target languages.

    Returns:
        dict: The view dict.
    """
    return {
        "title": {"type": "plain_text", "text": "Direct Login"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "blocks": [
            {
                "type": "input",
                "block_id": "email",
                "element": {
                    "type": "email_text_input",
                    "action_id": "email",
                    "placeholder": {"type": "plain_text", "text": "Email"},
                },
                "label": {"type": "plain_text", "text": "Email"},
                "optional": False,
            },
            {
                "type": "input",
                "block_id": "firstName",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "firstName",
                    "placeholder": {"type": "plain_text", "text": "First Name"},
                    "min_length": 3,
                    "max_length": 50,
                },
                "label": {"type": "plain_text", "text": "First Name"},
                "optional": False,
            },
            {
                "type": "input",
                "block_id": "lastName",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "lastName",
                    "placeholder": {"type": "plain_text", "text": "Last Name"},
                    "min_length": 3,
                    "max_length": 50,
                },
                "label": {"type": "plain_text", "text": "Last Name"},
                "optional": False,
            },
        ],
        "type": "modal",
        "callback_id": "login_sso",
    }

def cancel_job_modal(
                        client_name: str,
                    ) -> dict[str, Any]:
    """The template for the modal to cancel TJ by insert number and submit a search request

    Args:
        client_name (str): The user's LanguageCloud username.

    Returns:
        dict: The view dict.
    """

    return {
        "type": "modal",
        "callback_id": "cancel_job",
        "title": {"type": "plain_text", "text": "Cancel Job"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"You are cancel a job as `{client_name}`.",
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
                        "text": "Your TJ number",
                        "emoji": True,
                    },
                    "max_length": 100,
                },
                "label": {
                    "type": "plain_text",
                    "text": "Your job TJ number",
                    "emoji": True,
                },
            },
        ],
    }

def settings_auto_translate_view(
    initial_channels: list[str] | None = None, initial_langs: list[str] | None = None
) -> dict[str, Any]:
    # TODO: Filter conversations by access?
    # TODO: Detect message max length
    # TODO: Detect message formatting, emojis
    # TODO: 429 rate limiting
    # TODO: Max characters (5000?)
    language_options = get_auto_translate_language_options()
    initial_channels = initial_channels or []
    initial_lang_options = (
        filter_auto_translate_language_options(initial_langs) if initial_langs else []
    )

    return {
        "type": "modal",
        "callback_id": "settings_auto_translate",
        "title": {"type": "plain_text", "text": "Translation Settings"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "channels",
                "element": {
                    "type": "multi_conversations_select",
                    "action_id": "channels",
                    "placeholder": {"type": "plain_text", "text": "Select channel(s)"},
                    "initial_conversations": initial_channels,
                    "filter": {
                        "include": ["public", "private"],
                        "exclude_bot_users": True,
                    },
                },
                "label": {
                    "type": "plain_text",
                    "text": "Channels",
                    "emoji": True,
                },
                "hint": {
                    "type": "plain_text",
                    "text": "Important: Straker must be a member in the chosen channel or DM",
                },
                "optional": True,
            },
            {
                "type": "input",
                "block_id": "languages",
                "element": {
                    "type": "multi_static_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Choose language(s)",
                    },
                    "options": language_options,
                    **(
                        {"initial_options": initial_lang_options}
                        if initial_lang_options
                        else {}
                    ),
                    "action_id": "languages",
                    "max_selected_items": 3,
                },
                "label": {"type": "plain_text", "text": "Language", "emoji": True},
                "hint": {
                    "type": "plain_text",
                    "text": "Automatically translate messages into these language(s)",
                },
                "optional": True,
            },
        ],
    }
