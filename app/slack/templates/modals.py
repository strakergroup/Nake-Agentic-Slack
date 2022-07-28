from slack_sdk.web.async_client import AsyncWebClient


async def new_job_modal(client: AsyncWebClient, trigger_id: str, client_name: str) -> None:
    await client.views_open(
        trigger_id=trigger_id,
        view={
            "type": "modal",
            "title": {
                "type": "plain_text",
                "text": "New Translation Job"
            },
            "submit": {
                "type": "plain_text",
                "text": "Submit"
            },
            "close": {
                "type": "plain_text",
                "text": "Close"
            },
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"You are submitting a new job as `{client_name}`.",
                        "verbatim": True
                    }
                },
                {
                    "type": "input",
                    "element": {
                        "type": "static_select",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Select a group",
                            "emoji": True
                        },
                        # TODO get real groups
                        "options": [
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Test Group 1",
                                    "emoji": False
                                },
                                "value": "test_group_1"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Test Group 2",
                                    "emoji": False
                                },
                                "value": "test_group_2"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Test Group 3",
                                    "emoji": False
                                },
                                "value": "test_group_3"
                            }
                        ],
                        "action_id": "new_job_group_static_select"
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "Group",
                        "emoji": True
                    }
                },
                {
                    "type": "input",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "new_job_reference_plain_text"
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "Your reference",
                        "emoji": True
                    }
                },
                {
                    "type": "input",
                    "element": {
                        "type": "static_select",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Select a type",
                            "emoji": True
                        },
                        "options": [
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Document",
                                    "emoji": False
                                },
                                "value": "document"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Media",
                                    "emoji": False
                                },
                                "value": "media"
                            }
                        ],
                        "initial_option": {
                            "text": {
                                "type": "plain_text",
                                "text": "Document",
                                "emoji": False
                            },
                            "value": "document"
                        },
                        "action_id": "new_job_type_static_select"
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "Type",
                        "emoji": True
                    }
                },
                {
                    "type": "input",
                    "element": {
                        "type": "datepicker",
                        # "initial_date": "1990-04-28",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Select a date",
                            "emoji": True
                        },
                        "action_id": "new_job_date_due_datepicker"
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "Due date",
                        "emoji": True
                    }
                },
                {
                    "type": "input",
                    "element": {
                        "type": "static_select",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Select a service",
                            "emoji": True
                        },
                        "options": [
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Translation",
                                    "emoji": False
                                },
                                "value": "Translation"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Translation + Edit",
                                    "emoji": False
                                },
                                "value": "Translation + Edit"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Edit Only",
                                    "emoji": False
                                },
                                "value": "Edit"
                            }
                        ],
                        "initial_option": {
                            "text": {
                                "type": "plain_text",
                                "text": "Translation",
                                "emoji": False
                            },
                            "value": "Translation"
                        },
                        "action_id": "new_job_service_static_select"
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "Service type",
                        "emoji": True
                    }
                },
                {
                    "type": "input",
                    "element": {
                        "type": "static_select",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Select a category",
                            "emoji": True
                        },
                        "option_groups": [
                            {
                                "label": {
                                    "type": "plain_text",
                                    "text": "Advertising/Marketing"
                                },
                                "options": [
                                    {
                                        "text": {
                                            "type": "plain_text",
                                            "text": "Art/Literary",
                                            "emoji": False
                                        },
                                        "value": "art_literary"
                                    },
                                    {
                                        "text": {
                                            "type": "plain_text",
                                            "text": "Cosmetics",
                                            "emoji": False
                                        },
                                        "value": "cosmetics"
                                    },
                                    {
                                        "text": {
                                            "type": "plain_text",
                                            "text": "Cultural",
                                            "emoji": False
                                        },
                                        "value": "cultural"
                                    }
                                ]
                            },
                            {
                                "label": {
                                    "type": "plain_text",
                                    "text": "Finance, Business & HR"
                                },
                                "options": [
                                    {
                                        "text": {
                                            "type": "plain_text",
                                            "text": "Accounting",
                                            "emoji": False
                                        },
                                        "value": "accounting"
                                    },
                                    {
                                        "text": {
                                            "type": "plain_text",
                                            "text": "Banking",
                                            "emoji": False
                                        },
                                        "value": "banking"
                                    },
                                    {
                                        "text": {
                                            "type": "plain_text",
                                            "text": "Business",
                                            "emoji": False
                                        },
                                        "value": "business"
                                    }
                                ]
                            }
                        ],
                        "action_id": "new_job_category_static_select"
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "Category",
                        "emoji": True
                    }
                },
                {
                    "type": "input",
                    "element": {
                        "type": "static_select",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Select a source language",
                            "emoji": True
                        },
                        "options": [
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Arabic",
                                    "emoji": False
                                },
                                "value": "arabic"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Chinese Simplified",
                                    "emoji": False
                                },
                                "value": "chinese_simplified"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Chinese Traditional",
                                    "emoji": False
                                },
                                "value": "chinese_traditional"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "English (UK)",
                                    "emoji": False
                                },
                                "value": "english_uk"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "English (US)",
                                    "emoji": False
                                },
                                "value": "english_us"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "French",
                                    "emoji": False
                                },
                                "value": "french"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "German",
                                    "emoji": False
                                },
                                "value": "german"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Spanish",
                                    "emoji": False
                                },
                                "value": "spanish"
                            }
                        ],
                        "action_id": "new_job_source_static_select"
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "Source language",
                        "emoji": True
                    }
                },
                {
                    "type": "input",
                    "element": {
                        "type": "multi_static_select",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Select language(s)",
                            "emoji": True
                        },
                        "options": [
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Arabic",
                                    "emoji": False
                                },
                                "value": "arabic"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Chinese Simplified",
                                    "emoji": False
                                },
                                "value": "chinese_simplified"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Chinese Traditional",
                                    "emoji": False
                                },
                                "value": "chinese_traditional"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "English (UK)",
                                    "emoji": False
                                },
                                "value": "english_uk"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "English (US)",
                                    "emoji": False
                                },
                                "value": "english_us"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "French",
                                    "emoji": False
                                },
                                "value": "french"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "German",
                                    "emoji": False
                                },
                                "value": "german"
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Spanish",
                                    "emoji": False
                                },
                                "value": "spanish"
                            }
                        ],
                        "action_id": "new_job_target_multi_static_select"
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "Target language(s)",
                        "emoji": True
                    }
                }
            ]
        }
    )
