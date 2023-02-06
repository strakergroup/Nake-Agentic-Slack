"""Templates for individual Slack blocks."""

from typing import Any
from ray_sdk.api.v3.models import Quote
from ...ray.utils import (
    get_job_url,
    format_currency,
    format_currency_symbol,
)

def job_deltaray_link_block(job_uuid: str, client_id: str) -> dict[str, Any]:
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "View this job in DeltaRAY",
                    "emoji": True,
                },
                "style": "primary",
                "url": get_job_url(job_uuid, client_id),
                "action_id": "link",
            }
        ],
    }

def quote_message_block(quote: Quote, job_url: str) -> [dict[str, Any]]:
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
                quote.quote.tl[lang.code].price
                if lang.code in quote.quote.tl
                else 0.0
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
                            "This action requires you to be logged in to DeltaRAY.",
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
                        "text": "View in Deltaray",
                        "emoji": True,
                    },
                    "url": job_url,
                    "action_id": "link_2",
                },
            ],
        },
    ]
