"""Templates for individual Slack blocks."""
# Ignore line too long lint errors
# flake8: noqa

from typing import Any
from ray_sdk.api.v3.models import Quote
from ...auth.connector import (
    get_language_cloud_connect_url,
    RayConnection,
    encrpyt_slack_sso_token,
)
from ...config import domains, config, Environment
from ...ray.utils import (
    get_job_url,
    format_currency,
    format_currency_symbol,
)


def home_auth_blocks(
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
    channel_id: str,
    ray_connection: RayConnection | None,
) -> list[dict[str, Any]]:
    """The blocks in the Home tab which displays the LanguageCloud connection
    details or asks the user to connect their LanguageCloud account.
    """
    if isinstance(ray_connection, RayConnection) and ray_connection.client:
        super_group_names = [group.name for group in ray_connection.super_group]
        super_group_names_str = ", ".join(super_group_names)
        text = f"Your Slack account <@{user_id}> is connected with: <{domains.languagecloud}|{ray_connection.client.username}>."
        if ray_connection.client.sso:
            text = f"Your Slack account <@{user_id}> is connected with: *{ray_connection.client.username}*."
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Your Slack workspace is connected with: *{super_group_names_str}*.",
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
    msg = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Connect your LanguageCloud account to get details about your translation jobs.",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Connect LanguageCloud account",
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
    if (config.environment == Environment.production):
        e_id = 'EUJJ37YFR'
        t_id = 'T0360HUQKS9'
    else:
        e_id = "E04RDMG8XP1"
        t_id = "T02FDFCGK"
    if enterprise_id:
        if enterprise_id == e_id:
            msg[1]["elements"].append(
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Direct Login",
                    },
                    "style": "primary",
                    "action_id": "login_sso",
                }
            )
    elif team_id == t_id:
        msg[1]["elements"].append(
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "Direct Login",
                },
                "style": "primary",
                "action_id": "login_sso",
            }
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
                    "text": "View this job in LanguageCloud",
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
        f"within {quote.turnaround_days} days" if quote.turnaround_days > 0 else ""
    )
    # Show "incl. tax" next to the total cost if > the sum of the individual language prices.
    incl_tax = quote.quote.quote != quote.quote.quote_nett
    # Show prices for individual languages (if they exist).
    lang_price_blocks = []
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
                    "text": f"*Source Language:*\n{quote.sl.label}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Turnaround Time:*\n{turnaround_time}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Service:*\n{quote.service}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Client Reference:*\n{quote.client_reference}",
                },
            ],
        },
        {"type": "divider"},
        *lang_price_blocks,
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Total Cost ({currency})*: {quote_formatted} {'(incl. tax)' if incl_tax else ''}",
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
                        "text": "Accept Quote",
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
                        "text": "Cancel",
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
                            "text": "Are you sure you want to cancel this quote?\n\n"
                            "This action requires you to be logged in to LanguageCloud.",
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
                        "text": "View in LanguageCloud",
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
    status = (
        f"*In Progress Jobs*\n{predictions['in_progress']} job(s) currently in progress"
    )
    if predictions["on_time"] and predictions["late"] and predictions["over_due"]:
        aPredictions = []
        if predictions["on_time"]:
            aPredictions.append(
                f"*In Progress Jobs*\n:large_green_circle: *{predictions['on_time']} job(s)* are predicted to be on-time"
            )
        if predictions["on_time"]:
            aPredictions.append(
                f"*In Progress Jobs*\n:large_orange_circle: *{predictions['late']} job(s)* have been flagged as caution"
            )
        if predictions["over_due"]:
            aPredictions.append(
                f"*In Progress Jobs*\n:large_red_circle: *{predictions['over_due']} job(s)* are overdue"
            )
        status = "\n".join(aPredictions)
    return status


def job_prediction_block(prediction: str) -> dict:
    if "behind schedule" in prediction:
        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": prediction,
            },
            "accessory": {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "emoji": True,
                    "text": "Why?",
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
                "text": prediction,
            },
        }
    else:
        return {}
