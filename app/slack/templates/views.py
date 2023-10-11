"""Slack view templates (modals, home tab)."""
# Ignore line too long lint errors
# flake8: noqa

from typing import Any
from slack_bolt.context.async_context import AsyncBoltContext

from .blocks import home_auth_blocks
from ..select_options import map_file_options
from ...auth.connector import RayConnection
from ...config import domains


def home_view(
    context: AsyncBoltContext, app_id: str, rayConnection: RayConnection
) -> dict[str, Any]:
    message_url = f"slack://app?team={context['team_id']}&id={app_id}&tab=messages"
    return {
        "type": "home",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Welcome to RAY Translate for Slack!",
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
                    }
                    # {
                    #     "type": "button",
                    #     "text": {
                    #         "type": "plain_text",
                    #         "emoji": True,
                    #         "text": "👏 Favorite Languages"
                    #     },
                    #     "value": "click_me_123"
                    # }
                ],
            },
            {"type": "divider"},
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Give us your feedback"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Straker Community is a place for people who use Straker's users to provide feedback, and help each other get the most out of our platform. It's also a place for us to talk about the latest and greatest Straker LanguageCloud and Enterprise features, provide updates, and engage with customers like you!",
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Learn More", "emoji": True},
                    "action_id": "link_0",
                    "url": "https://strakergroup.frill.co/b/6m51y2vz/feature-ideas",
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
                    "text": f"You are searching a job as `{client_name}`.",
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
                    "max_length": 100,
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
    initial_files = (
        map_file_options(initial_files[:max_selected_files]) if initial_files else []
    )
    file_options = map_file_options(file_options[:100]) if file_options else []
    # Add the initial files to the file options if they are not there already.
    for file in initial_files:
        if not any(file["value"] == opt["value"] for opt in file_options):
            file_options.append(file)
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
            "action_id": "file_options",
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
            "action_id": "file_options",
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
