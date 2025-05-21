"""Templates for individual Slack blocks."""

from typing import Any
from ray_sdk.api.v3.models import Quote

from app.ray.events.models import JobQuoteCreatedEvent
from app.slack.utils import segment_quality_score
from ...auth.connector import (
    get_language_cloud_connect_url,
    RayConnection,
)
from ...config import domains
from ...ray.utils import (
    get_job_url,
    format_currency,
    format_currency_symbol,
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
    # msg: list[dict[str, Any]] = [
    #     {
    #         "type": "section",
    #         "text": {
    #             "type": "mrkdwn",
    #             "text": _(
    #                 "Connect your account to get details about your translation jobs."
    #             ),
    #         },
    #     },
    #     {
    #         "type": "actions",
    #         "elements": [],
    #     },
    # ]
    if is_ibm_enterprise(enterprise_id):
        msg: list[dict[str, Any]] = [
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
    else:
        msg: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _(
                        "\n\nVerify (Quality Evaluation) allows you:\n\n    • Translate content using AI translation.\n    • Assess the quality of the translation to determine the reliability of the AI-translated content along with any existing translation memory you may have with Straker.\n    • Determine whether the translated content is suitable for use or requires further human verification."
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [],
            },
        ]
        msg[1]["elements"].insert(
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
        ),
    return msg


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
    lang_price_blocks: list[dict[str, Any]] = []
    if quote.quote.tl:
        lang_price_blocks = [
            {
                "type": "section",
                "fields": [],
            },
            {"type": "divider"},
        ]
        for lang in quote.tl:
            lang_price = (
                quote.quote.tl[lang.code].price if lang.code in quote.quote.tl else 0.0
            )
            lang_price_formatted = format_currency(lang_price, quote.quote.currency)
            target_lang = _(lang.label)
            lang_price_blocks[0]["fields"].append(
                {
                    "type": "mrkdwn",
                    "text": f"*{target_lang}:*\n{lang_price_formatted}",
                }
            )
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


def get_progess_text(predictions: dict) -> str:
    """Returns the progress text for the job."""
    in_progress_predictions = predictions["in_progress"]
    on_time_predictions = predictions["on_time"]
    late_predictions = predictions["late"]
    over_due_predictions = predictions["over_due"]
    status = _(
        "*In Progress Jobs*\n{in_progress_predictions} job(s) currently in progress"
    )
    if on_time_predictions and late_predictions and over_due_predictions:
        aPredictions = []
        if on_time_predictions:
            aPredictions.append(
                _(
                    "*In Progress Jobs*\n:large_green_circle: *{on_time_predictions} job(s)* are predicted to be on-time"
                )
            )
        if late_predictions:
            aPredictions.append(
                _(
                    "*In Progress Jobs*\n:large_orange_circle: *{late_predictions} job(s)* have been flagged as caution"
                )
            )
        if over_due_predictions:
            aPredictions.append(
                _(
                    "*In Progress Jobs*\n:large_red_circle: *{over_due_predictions} job(s)* are overdue"
                )
            )
        status = "\n".join(aPredictions)
    return status


def job_prediction_block(
    prediction: str, value: int = 0, emorji: str = ":large_orange_circle:"
) -> dict:
    if "behind schedule" in prediction:
        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _(prediction),
            },
            "accessory": {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "emoji": True,
                    "text": _("Why?"),
                },
                "action_id": "delay_info",
                "value": "delay_info",
            },
        }
    elif "on time" in prediction or "on-time" in prediction:
        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _(prediction),
            },
        }
    else:
        return {}


def verify_job_blocks(
    summary: str,
    source_file_uuid: str,
    report: dict[str, Any] | None,
    lang_name: str,
    language_uuid: str,
    costs: list[dict[str, Any]],
    optional: bool,
    human_job_status: str,
) -> dict[str, Any]:
    """The blocks for the verification job."""
    cost = 0.00
    for item in costs:
        if item["language_uuid"] == language_uuid:
            cost = item["service_list"][0]["estimated_cost"]
            break

    cost_block = {
        "type": "input",
        "block_id": f"verification_checkbox_{language_uuid}_{source_file_uuid}",
        "label": {
            "type": "plain_text",
            "text": _(lang_name),
        },
        "element": {
            "type": "checkboxes",
            "options": [
                {
                    "text": {
                        "type": "mrkdwn",
                        "text": f"USD${cost:.2f}",
                    },
                    "value": f"{source_file_uuid}:{language_uuid}",
                },
            ],
            "initial_options": [
                {
                    "text": {
                        "type": "mrkdwn",
                        "text": f"USD${cost:.2f}",
                    },
                    "value": f"{source_file_uuid}:{language_uuid}",
                },
            ],
            "action_id": "verification_checkbox_action",
        },
        "optional": optional,
    }

    if human_job_status:
        lang_label = f"*{_(lang_name)}*\n"
        cost_block = {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _(
                    "{lang_label} Human verification has been submitted for this language."
                ),
            },
        }

    if report is None:
        return [
            cost_block,
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": _("*Summary:*\n{summary}")},
                ],
            },
            {
                "type": "divider",
            },
        ]

    segment_count = sum(report["count"].values())
    if segment_count == 0:
        bad = good = best = acceptable = memory_percentage = 0
    else:
        counts = report["count"]
        bad = (counts["bad"] / segment_count) * 100
        good = (counts["good"] / segment_count) * 100
        best = (counts["best"] / segment_count) * 100
        acceptable = (counts["acceptable"] / segment_count) * 100
        memory_percentage = (counts["translation_memory"] / segment_count) * 100

    report_message = (
        f":large_blue_square: {_('Translation Memory')}: {round(memory_percentage)}%\n"
    )
    report_message += f":large_green_square: {_('Best')}: {round(best)}%\n"
    report_message += f":large_yellow_square: {_('Good')}: {round(good)}%\n"
    report_message += f":large_orange_square: {_('Acceptable')}: {round(acceptable)}%\n"
    report_message += f":large_red_square: {_('Bad')}: {round(bad)}%"

    return [
        cost_block,
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": _("*Summary:*\n{summary}")},
                {"type": "mrkdwn", "text": _("*Overall Score:*\n{report_message}")},
            ],
        },
        {
            "type": "divider",
        },
    ]


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
