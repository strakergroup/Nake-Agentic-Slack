"""Slack view templates (modals, home tab)."""
# Ignore line too long lint errors
# flake8: noqa

from typing import Any

from ..select_options import map_file_options


def new_job_modal(
    client_name: str, files: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The template for the modal to submit a new translation job. The user can
    select the files they want to translate and enter the job details, e.g.
    category, source and target languages.

    Args:
        client_name (str): The user's DeltaRay username.
        files (list[dict] | None, optional): A list of file objects to initally select. Defaults to None.

    Returns:
        dict: The view dict.
    """
    initial_files = map_file_options(files) if files else []
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
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Select the files you want to translate",
                },
            },
            {
                "type": "input",
                "block_id": "conversation",
                "element": {
                    "type": "conversations_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select a conversation",
                        "emoji": True,
                    },
                    "filter": {
                        # TODO: bots cannot access private,im,mpim, use user token
                        # to access all
                        "include": ["public", "im"],
                        "exclude_external_shared_channels": True,
                    },
                    "action_id": "select_conversation",
                    "default_to_current_conversation": True,
                },
                "label": {
                    "type": "plain_text",
                    "text": "Location of the file(s)",
                    "emoji": True,
                },
                "optional": True,
            },
            {
                "type": "input",
                "block_id": "files",
                "element": {
                    "type": "multi_external_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select file(s)",
                        "emoji": True,
                    },
                    "action_id": "file_options",
                    "min_query_length": 0,
                    "initial_options": initial_files,
                    "max_selected_items": 10,
                },
                "label": {
                    "type": "plain_text",
                    "text": "File(s) to translate",
                    "emoji": True,
                },
            },
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Job details",
                },
            },
            {
                "type": "input",
                "block_id": "reference",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "reference",
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
                    "text": "What is the original language of the file(s)?",
                },
            },
            {
                "type": "input",
                "block_id": "target_langs",
                "element": {
                    "type": "multi_external_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select language(s)",
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
                "block_id": "job_type",
                "element": {
                    "type": "static_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select a type",
                        "emoji": True,
                    },
                    "options": [
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Document",
                                "emoji": False,
                            },
                            "value": "document",
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Media",
                                "emoji": False,
                            },
                            "value": "media",
                        },
                    ],
                    "initial_option": {
                        "text": {
                            "type": "plain_text",
                            "text": "Document",
                            "emoji": False,
                        },
                        "value": "document",
                    },
                    "action_id": "job_type",
                },
                "label": {"type": "plain_text", "text": "Type", "emoji": True},
            },
            {
                "type": "input",
                "block_id": "target_date",
                "element": {
                    "type": "datepicker",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select a date",
                        "emoji": True,
                    },
                    "action_id": "target_date",
                },
                "label": {"type": "plain_text", "text": "Target date", "emoji": True},
            },
            {
                "type": "input",
                "block_id": "workflow",
                "element": {
                    "type": "static_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select a workflow",
                        "emoji": True,
                    },
                    "options": [
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Translation",
                                "emoji": False,
                            },
                            "value": "TRANSLATION",
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Translation + Review",
                                "emoji": False,
                            },
                            "value": "TRANSLATION_REVIEW",
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Translation + Validation",
                                "emoji": False,
                            },
                            "value": "TRANSLATION_VALIDATION",
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Translation + Review + Validation",
                                "emoji": False,
                            },
                            "value": "TRANSLATION_REVIEW_VALIDATION",
                        },
                    ],
                    "initial_option": {
                        "text": {
                            "type": "plain_text",
                            "text": "Translation",
                            "emoji": False,
                        },
                        "value": "TRANSLATION",
                    },
                    "action_id": "workflow",
                },
                "label": {"type": "plain_text", "text": "Service type", "emoji": True},
            },
            {
                "type": "input",
                "block_id": "category",
                "element": {
                    "type": "static_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select a category",
                        "emoji": True,
                    },
                    "option_groups": [
                        {
                            "label": {
                                "type": "plain_text",
                                "text": "Advertising/Marketing",
                            },
                            "options": [
                                {
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Art/Literary",
                                        "emoji": False,
                                    },
                                    "value": "art_literary",
                                },
                                {
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Cosmetics",
                                        "emoji": False,
                                    },
                                    "value": "cosmetics",
                                },
                                {
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Cultural",
                                        "emoji": False,
                                    },
                                    "value": "cultural",
                                },
                            ],
                        },
                        {
                            "label": {
                                "type": "plain_text",
                                "text": "Finance, Business & HR",
                            },
                            "options": [
                                {
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Accounting",
                                        "emoji": False,
                                    },
                                    "value": "accounting",
                                },
                                {
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Banking",
                                        "emoji": False,
                                    },
                                    "value": "banking",
                                },
                                {
                                    "text": {
                                        "type": "plain_text",
                                        "text": "Business",
                                        "emoji": False,
                                    },
                                    "value": "business",
                                },
                            ],
                        },
                    ],
                    "action_id": "category",
                },
                "label": {"type": "plain_text", "text": "Category", "emoji": True},
            },
        ],
    }
