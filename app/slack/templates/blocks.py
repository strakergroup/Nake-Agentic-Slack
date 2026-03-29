"""Templates for individual Slack blocks."""

import math
from datetime import datetime, timedelta
from typing import Any

from ray_sdk.api.v3.models import Quote

from app.constants import HUMAN_EVALUATION_WORKFLOW_UUID
from app.ray.events.models import JobQuoteCreatedEvent
from app.slack.select_options import get_languages_sync
from app.slack.utils import calculate_evaluation_percentages, segment_quality_score

from ...auth.connector import (
    RayConnection,
    get_language_cloud_connect_url,
)
from ...config import domains
from ...ray.utils import (
    format_currency,
    format_currency_symbol,
    get_job_url,
    is_ibm_enterprise,
)
from ...translate import _


def home_auth_blocks(
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
    channel_id: str | None,
    ray_connection: RayConnection | None,
) -> list[dict[str, Any]]:
    """The blocks in the Home tab which displays the LanguageCloud connection
    details or asks the user to connect their LanguageCloud account.
    """
    is_ibm = is_ibm_enterprise(enterprise_id)
    if isinstance(ray_connection, RayConnection) and ray_connection.client:
        super_group_names = [group.name for group in ray_connection.super_group]
        super_group_names_str = ", ".join(super_group_names)
        user_id_str = f"<@{user_id}>"
        domain_url = f"<{domains.languagecloud}|{ray_connection.client.username}>"
        enable_verify = ray_connection.super_group[0].enable_verify_in_slack
        text = _("Your Slack account {user_id_str} is connected with: {domain_url}.")
        if ray_connection.client.sso:
            text = _("Your Slack account {user_id_str} is connected.")
        # To show the verify enabled status/message in the home tab
        # if enable_verify:
        #     text += _("\n\n Your Slack account is connected to *LangaugeCloud* and *Verify*.")
        # else:
        #     text += _("\n\n Your Slack account is connected to *LangaugeCloud*.")
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        "Your Slack workspace is connected with: *{super_group_names_str}*."
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text,
                },
            },
        ]
    if is_ibm:
        blocks: list[dict[str, Any]] = [
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
                "elements": [],
            },
        ]
        blocks[1]["elements"].insert(
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
    else:
        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        "\n\nVerify (Quality Evaluation) allows you to:\n\n    • Translate content using AI translation.\n    • Assess the quality of the translation to determine the reliability of the AI-translated content along with any existing translation memory you may have with Straker.\n    • Determine whether the translated content is suitable for use or requires further human verification."
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [],
            },
        ]
        blocks[1]["elements"].insert(
            0,
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": _("Connect to Verify"),
                },
                "style": "primary",
                "url": get_language_cloud_connect_url(
                    user_id, team_id, enterprise_id, channel_id or user_id
                ),
                "action_id": "login",
            },
        )

    return blocks


def job_link_block(job_uuid: str, client_id: str) -> dict[str, Any]:
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": _("View this job"),
                    "emoji": True,
                },
                "style": "primary",
                "url": get_job_url(job_uuid, client_id),
                "action_id": "link",
            }
        ],
    }


def quote_message_block(
    quote: Quote | JobQuoteCreatedEvent, job_url: str, is_ibm: bool
) -> list[dict[str, Any]]:
    currency = format_currency_symbol(quote.quote.currency)
    quote_formatted = format_currency(quote.quote.quote, quote.quote.currency)
    turnaround_time = (
        _("within {quote.turnaround_days} days") if quote.turnaround_days > 0 else ""
    )
    # Show "incl. tax" next to the total cost if > the sum of the individual language prices.
    incl_tax = quote.quote.quote != quote.quote.quote_nett
    # Show prices for individual languages (if they exist).
    # Slack limits section blocks to 10 fields, so we need to split across multiple blocks
    lang_price_blocks: list[dict[str, Any]] = []
    if quote.quote.tl:
        MAX_FIELDS_PER_BLOCK = 10
        fields_blocks = []
        current_fields = []

        for lang in quote.tl:
            lang_price = (
                quote.quote.tl[lang.code].price if lang.code in quote.quote.tl else 0.0
            )
            lang_price_formatted = format_currency(lang_price, quote.quote.currency)
            target_lang = _(lang.label)
            current_fields.append(
                {
                    "type": "mrkdwn",
                    "text": f"*{target_lang}:*\n{lang_price_formatted}",
                }
            )

            # Create a new block when we reach the limit
            if len(current_fields) >= MAX_FIELDS_PER_BLOCK:
                fields_blocks.append(
                    {
                        "type": "section",
                        "fields": current_fields,
                    }
                )
                current_fields = []

        # Add remaining fields if any
        if current_fields:
            fields_blocks.append(
                {
                    "type": "section",
                    "fields": current_fields,
                }
            )

        # Build the blocks list with dividers between sections (but not after the last one)
        lang_price_blocks = []
        for i, block in enumerate(fields_blocks):
            lang_price_blocks.append(block)
            if (
                i < len(fields_blocks) - 1
            ):  # Add divider between blocks, but not after the last one
                lang_price_blocks.append({"type": "divider"})

        # Add a divider after all language price blocks
        if fields_blocks:
            lang_price_blocks.append({"type": "divider"})
    actions_block = [
        {
            "type": "button",
            "text": {
                "type": "plain_text",
                "emoji": True,
                "text": _("Accept Quote"),
            },
            "style": "primary",
            "url": quote.quote.quote_accept_url,
            "action_id": "link",
        },
        # {
        #     "type": "button",
        #     "text": {
        #         "type": "plain_text",
        #         "emoji": True,
        #         "text": _("Cancel"),
        #     },
        #     "style": "danger",
        #     "url": quote.quote.quote_cancel_url,
        #     "action_id": "link_1",
        #     "confirm": {
        #         "title": {
        #             "type": "plain_text",
        #             "text": "Cancel Quote",
        #         },
        #         "text": {
        #             "type": "plain_text",
        #             "text": _(
        #                 "Are you sure you want to cancel this quote?\n\n"
        #                 + "This action requires you to be logged in to LanguageCloud."
        #             ),
        #         },
        #         "confirm": {"type": "plain_text", "text": "Yes"},
        #         "deny": {
        #             "type": "plain_text",
        #             "text": "No",
        #         },
        #     },
        # },
    ]
    if not is_ibm:
        actions_block.append(
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": _("View in Verify"),
                    "emoji": True,
                },
                "url": job_url,
                "action_id": "link_2",
            },
        )
    source_lang = _(quote.sl.label)
    service_tra = _(quote.service)
    return [
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": _("*Source Language:*\n{source_lang}"),
                },
                {
                    "type": "mrkdwn",
                    "text": _("*Turnaround Time:*\n{turnaround_time}"),
                },
                {
                    "type": "mrkdwn",
                    "text": _("*Service:*\n{service_tra}"),
                },
                {
                    "type": "mrkdwn",
                    "text": _("*Client Reference:*\n{quote.client_reference}"),
                },
            ],
        },
        {"type": "divider"},
        *lang_price_blocks,
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _("*Total Cost ({currency})*: ")
                + f"{quote_formatted} {'(incl. tax)' if incl_tax else ''}",
            },
        },
        {"type": "divider"},
        {"type": "actions", "elements": actions_block},
    ]


def verify_quote_blocks(
    job: dict[str, Any],
    costs: list[dict[str, Any]],
    selectable: bool = True,
):
    source_files = job["source_files"]
    workflow_uuid = job["workflow_uuid"]
    blocks: list[dict[str, Any]] = []
    total_cost = 0.0
    for file in source_files:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":paperclip: *{file['filename']}*",
                },
            }
        )
        for lang in job["target_languages"]:
            target_file = next(
                (
                    target_file
                    for target_file in file["target_files"]
                    if target_file["language_uuid"] == lang["uuid"]
                ),
                None,
            )
            cost = 0.00
            estimated_time = 0
            for item in costs:
                if (
                    item["language_uuid"] == lang["uuid"]
                    and item["file_uuid"] == file["file_uuid"]
                ):
                    cost = item["service_list"][0]["estimated_cost"]
                    estimated_time = item["service_list"][0]["time_estimate_days"]
                    break
            if target_file and target_file.get("human_job_status", ""):
                if target_file["human_job_status"] == "Submitted":
                    lang_label = f"*{_(lang['name'])}*\n"
                    cost_block = {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": _(
                                "{lang_label} Human translation has been submitted for this language."
                            ),
                        },
                    }
                    if not selectable:
                        total_cost += cost
                    blocks.append(cost_block)
                elif target_file["human_job_status"] == "Cancelled":
                    lang_label = f"*{_(lang['name'])}*\n"
                    cost_block = {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": _(
                                "{lang_label} Human translation has been cancelled for this language."
                            ),
                        },
                    }
                    blocks.append(cost_block)
            else:
                total_cost += cost
                report = None
                if workflow_uuid != HUMAN_EVALUATION_WORKFLOW_UUID:
                    if "report" in file and "evaluation_reports" in file["report"]:
                        report = next(
                            (
                                report
                                for report in file["report"]["evaluation_reports"]
                                if report["target_language"] == lang["uuid"]
                            ),
                            None,
                        )

                if selectable:
                    blocks.append(
                        {
                            "type": "actions",
                            "block_id": f"verification_checkbox_{lang['uuid']}_{file['file_uuid']}",
                            "elements": [
                                {
                                    "type": "checkboxes",
                                    "options": [
                                        {
                                            "text": {
                                                "type": "mrkdwn",
                                                "text": f"*{lang['name']}*: USD${cost:.2f}",
                                            },
                                            "value": f"{file['file_uuid']}:{lang['uuid']}:{str(estimated_time)}",
                                        },
                                    ],
                                    "initial_options": [
                                        {
                                            "text": {
                                                "type": "mrkdwn",
                                                "text": f"*{lang['name']}*: USD${cost:.2f}",
                                            },
                                            "value": f"{file['file_uuid']}:{lang['uuid']}:{str(estimated_time)}",
                                        },
                                    ],
                                    "action_id": "verification_checkbox_action",
                                },
                            ],
                        }
                    )
                    source_lang_uuid = file["report"]["language_uuid"]
                    all_langs = get_languages_sync()
                    if not all_langs:
                        # Fallback: return a default message if languages cache is empty
                        source_lang = {"name": "Unknown Language"}
                    else:
                        source_lang = next(
                            (
                                lang
                                for lang in all_langs
                                if lang["uuid"] == source_lang_uuid
                            ),
                            {"name": "Unknown Language"},
                        )
                    summary = job_summary_string(source_lang, lang, file)

                    if report:
                        pct = calculate_evaluation_percentages(report["count"])
                        report_message = f":large_blue_square: {_('Translation Memory')}: {pct['translation_memory']}%\n"
                        report_message += (
                            f":large_green_square: {_('Best')}: {pct['best']}%\n"
                        )
                        report_message += (
                            f":large_yellow_square: {_('Good')}: {pct['good']}%\n"
                        )
                        report_message += f":large_orange_square: {_('Acceptable')}: {pct['acceptable']}%\n"
                        report_message += (
                            f":large_red_square: {_('Bad')}: {pct['bad']}%"
                        )
                        blocks.append(
                            {
                                "type": "section",
                                "fields": [
                                    {
                                        "type": "mrkdwn",
                                        "text": _("*Summary:*\n{summary}"),
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": _("*Overall Score:*\n{report_message}"),
                                    },
                                ],
                            },
                        )
                    else:
                        blocks.append(
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": summary,
                                },
                            }
                        )
                else:
                    blocks.append(
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*{_(lang['name'])}*\n>USD ${cost:.2f}",
                            },
                        }
                    )
        blocks.append({"type": "divider"})
    # Group costs by file_uuid and multiply time_estimate_days by count for each group
    grouped_times = {}
    for cost_item in costs:
        key = cost_item["file_uuid"]
        time_estimate = cost_item["service_list"][0]["time_estimate_days"]
        if key not in grouped_times:
            grouped_times[key] = {"time_estimate": time_estimate, "count": 1}
        else:
            grouped_times[key]["count"] += 1
            if time_estimate > grouped_times[key]["time_estimate"]:
                grouped_times[key]["time_estimate"] = time_estimate

    # Calculate total time by multiplying max time estimate by count for each file
    total_estimated_days = math.ceil(
        sum(group["time_estimate"] * group["count"] for group in grouped_times.values())
    )

    # Calculate completion date
    completion_date = datetime.now() + timedelta(days=total_estimated_days)
    formatted_date = completion_date.strftime("%d %B %Y")

    blocks.append(
        {
            "type": "section",
            "block_id": "total_cost_block",
            "text": {
                "type": "mrkdwn",
                "text": _("*Total Cost*: USD ${total_cost:.2f}"),
            },
        }
    )
    blocks.append(
        {
            "type": "section",
            "block_id": "total_estimated_time_block",
            "text": {
                "type": "mrkdwn",
                "text": _("*Estimated Completion*: {formatted_date}"),
            },
        }
    )
    return blocks


def evaluate_success_blocks(
    job: dict[str, Any],
):
    source_files = job["source_files"]
    blocks: list[dict[str, Any]] = []
    for file in source_files:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":paperclip: *{file['filename']}*",
                },
            }
        )
        for lang in job["target_languages"]:
            target_file = next(
                (
                    target_file
                    for target_file in file["target_files"]
                    if target_file["language_uuid"] == lang["uuid"]
                ),
                None,
            )
            if target_file and target_file.get("human_job_status", ""):
                lang_label = f"*{_(lang['name'])}*\n"
                cost_block = {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _(
                            "{lang_label} Human translation has been submitted for this language."
                        ),
                    },
                }
                blocks.append(cost_block)
            else:
                report = None
                if "report" in file and "evaluation_reports" in file["report"]:
                    report = next(
                        (
                            report
                            for report in file["report"]["evaluation_reports"]
                            if report["target_language"] == lang["uuid"]
                        ),
                        None,
                    )
                source_lang_uuid = file["report"]["language_uuid"]
                all_langs = get_languages_sync()
                if not all_langs:
                    # Fallback: return a default message if languages cache is empty
                    source_lang = {"name": "Unknown Language"}
                else:
                    source_lang = next(
                        (
                            lang
                            for lang in all_langs
                            if lang["uuid"] == source_lang_uuid
                        ),
                        {"name": "Unknown Language"},
                    )
                summary = job_summary_string(source_lang, lang, file)

                if report:
                    pct = calculate_evaluation_percentages(report["count"])
                    report_message = f":large_blue_square: {_('Translation Memory')}: {pct['translation_memory']}%\n"
                    report_message += (
                        f":large_green_square: {_('Best')}: {pct['best']}%\n"
                    )
                    report_message += (
                        f":large_yellow_square: {_('Good')}: {pct['good']}%\n"
                    )
                    report_message += f":large_orange_square: {_('Acceptable')}: {pct['acceptable']}%\n"
                    report_message += f":large_red_square: {_('Bad')}: {pct['bad']}%"
                    blocks.append(
                        {
                            "type": "section",
                            "fields": [
                                {
                                    "type": "mrkdwn",
                                    "text": _("*Summary:*\n{summary}"),
                                },
                                {
                                    "type": "mrkdwn",
                                    "text": _("*Overall Score:*\n{report_message}"),
                                },
                            ],
                        },
                    )
                else:
                    blocks.append(
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": summary,
                            },
                        }
                    )
                target_file_uuid = ""
                for target_file in file["target_files"]:
                    if target_file["language_uuid"] == lang["uuid"]:
                        target_file_uuid = target_file["target_file_uuid"]
                        break
                if target_file_uuid:
                    blocks.append(
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": _("Download AI Translation"),
                                    },
                                    "value": target_file_uuid,
                                    "action_id": "download_ai_translation_action",
                                },
                            ],
                        },
                    )

    return blocks


def job_summary_string(
    source_lang: dict[str, Any], lang: dict[str, Any], file: dict[str, Any]
):
    """Returns the job summary string."""
    formatted_source_lang = _(source_lang["name"])
    formatted_target_lang = _(lang["name"])
    file_name = file["filename"]
    report = lang.get("report", None)

    formatted_score = (
        _(segment_quality_score(lang["report"]["score"])) if report else ""
    )
    return _(
        "Detected Source Language: {formatted_source_lang}\nTranslate to: {formatted_target_lang}\nFile Uploaded: {file_name}\n{formatted_score}"
    )


def job_summary_no_score(lang: dict[str, Any], file: dict[str, Any]):
    """Returns the job summary string."""
    formatted_target_lang = _(lang["name"])
    file_name = file["filename"]
    return _("Translate to: {formatted_target_lang}\nFile Uploaded: {file_name}\n")
