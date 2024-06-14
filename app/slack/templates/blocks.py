"""Templates for individual Slack blocks."""

from typing import Any
from ray_sdk.api.v3.models import Quote
from ...auth.connector import (
    get_language_cloud_connect_url,
    RayConnection,
)
from ...config import domains, config, Environment
from ...ray.utils import (
    get_job_url,
    format_currency,
    format_currency_symbol,
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
        text = "Your Slack account {user_id_str} is connected with: {domain_url}."
        if ray_connection.client.sso:
            text = "Your Slack account {user_id_str} is connected with: *{ray_connection.client.username}*."
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
                    "text": _(text),
                },
            },
        ]
    msg = [
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
                        user_id, team_id, enterprise_id, channel_id or user_id
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
        if enterprise_id == e_id:
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
    elif team_id == t_id:
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
    return msg


def job_link_block(job_uuid: str, client_id: str) -> dict[str, Any]:
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": _("View this job in LanguageCloud"),
                    "emoji": True,
                },
                "style": "primary",
                "url": get_job_url(job_uuid, client_id),
                "action_id": "link",
            }
        ],
    }


def quote_message_block(quote: Quote, job_url: str) -> list[dict[str, Any]]:
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
            lang_price_blocks[0]["fields"].append(
                {
                    "type": "mrkdwn",
                    "text": f"*{lang.label}:*\n{lang_price_formatted}",
                }
            )
    return [
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": _("*Source Language:*\n{quote.sl.label}"),
                },
                {
                    "type": "mrkdwn",
                    "text": _("*Turnaround Time:*\n{turnaround_time}"),
                },
                {
                    "type": "mrkdwn",
                    "text": _("*Service:*\n{quote.service}"),
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
                "text": _(
                    "*Total Cost ({currency})*: "
                ) + f"{quote_formatted} {'(incl. tax)' if incl_tax else ''}"
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
                        "text": _("Accept Quote"),
                    },
                    "style": "primary",
                    "url": quote.quote.quote_accept_url,
                    "action_id": "link",
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "emoji": True,
                        "text": _("Cancel"),
                    },
                    "style": "danger",
                    "url": quote.quote.quote_cancel_url,
                    "action_id": "link_1",
                    "confirm": {
                        "title": {
                            "type": "plain_text",
                            "text": "Cancel Quote",
                        },
                        "text": {
                            "type": "plain_text",
                            "text": _(
                                "Are you sure you want to cancel this quote?\n\n"
                                + "This action requires you to be logged in to LanguageCloud."
                            ),
                        },
                        "confirm": {"type": "plain_text", "text": "Yes"},
                        "deny": {
                            "type": "plain_text",
                            "text": "No",
                        },
                    },
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": _("View in LanguageCloud"),
                        "emoji": True,
                    },
                    "url": job_url,
                    "action_id": "link_2",
                },
            ],
        },
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


def job_prediction_block(prediction: str, value: int = 0, emorji: str = ':large_orange_circle:') -> dict:
    print("prediction", prediction)
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
