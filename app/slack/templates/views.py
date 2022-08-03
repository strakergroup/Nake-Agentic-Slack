from typing import Any
import json
from ..select_options import map_file_options


def new_job_modal(
    client_name: str, files: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The template for the modal to submit a new translation job. This is the
    first modal that is used to submit a new translation job, this modal is for
    the user to enter the job details, e.g. category, source and target languages.

    Args:
        client_name (str): The user's DeltaRay username.
        files (list[str] | None, optional): A list of file objects to initally
        select. Defaults to None.

    Returns:
        dict: The view dict.
    """
    # private_metadata string has max 3000 characters.
    private_metadata = {
        "files": map_file_options(files) if files else [],
    }
    return {
        "type": "modal",
        "callback_id": "new_job",
        "private_metadata": json.dumps(private_metadata),
        "title": {"type": "plain_text", "text": "New Translation Job"},
        "submit": {"type": "plain_text", "text": "Select Files"},
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
                "element": {
                    "type": "static_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select a group",
                        "emoji": True,
                    },
                    # TODO get real groups
                    "options": [
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Test Group 1",
                                "emoji": False,
                            },
                            "value": "test_group_1",
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Test Group 2",
                                "emoji": False,
                            },
                            "value": "test_group_2",
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Test Group 3",
                                "emoji": False,
                            },
                            "value": "test_group_3",
                        },
                    ],
                    "action_id": "new_job_group_static_select",
                },
                "label": {"type": "plain_text", "text": "Group", "emoji": True},
            },
            {
                "type": "input",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "new_job_reference_plain_text",
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
                    "action_id": "new_job_type_static_select",
                },
                "label": {"type": "plain_text", "text": "Type", "emoji": True},
            },
            {
                "type": "input",
                "element": {
                    "type": "datepicker",
                    # "initial_date": "1990-04-28",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select a date",
                        "emoji": True,
                    },
                    "action_id": "new_job_date_due_datepicker",
                },
                "label": {"type": "plain_text", "text": "Target date", "emoji": True},
            },
            {
                "type": "input",
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
                                "text": "Edit Only",
                                "emoji": False,
                            },
                            "value": "Edit",
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
                    "action_id": "new_job_service_static_select",
                },
                "label": {"type": "plain_text", "text": "Service type", "emoji": True},
            },
            {
                "type": "input",
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
                    "action_id": "new_job_category_static_select",
                },
                "label": {"type": "plain_text", "text": "Category", "emoji": True},
            },
            {
                "type": "input",
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
            },
            {
                "type": "input",
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
            },
        ],
    }


def new_job_files_modal(files: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The template for the modal to select files for a new translation job.
    This is the second modal that is used to submit a new job.

    Args:
        files (list[dict] | None, optional): A list of file options (not
        objects) to initially select. Defaults to None.

    Returns:
        dict: The view dict.
    """
    return {
        "type": "modal",
        "callback_id": "new_job_files",
        "title": {"type": "plain_text", "text": "New Translation Job"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Back"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Select the files you want to translate.",
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
                        # TODO: bots cannot access private,im,mpim, use user token to access all
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
                "element": {
                    "type": "multi_external_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select file(s)",
                        "emoji": True,
                    },
                    "action_id": "file_options",
                    "min_query_length": 0,
                    "initial_options": files or [],
                },
                "label": {
                    "type": "plain_text",
                    "text": "File(s) to translate",
                    "emoji": True,
                },
            },
        ],
    }
