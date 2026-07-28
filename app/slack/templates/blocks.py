"""Templates for individual Slack blocks."""

from datetime import datetime, timedelta
from typing import Any

from ray_sdk.api.v3.models import Quote

from app.constants import HUMAN_EVALUATION_WORKFLOW_UUID
from app.ray.events.models import JobQuoteCreatedEvent
from app.slack.select_options import get_languages_sync
from app.slack.utils import (
    calculate_evaluation_percentages,
    calculate_total_estimated_days,
    segment_quality_score,
)

from ...auth.connector import (
    RayConnection,
    get_language_cloud_connect_url,
)
from ...config import domains
from ...ray.utils import (
    format_currency,
    format_currency_symbol,
    format_slack_usd,
    get_job_url,
    is_ibm_enterprise,
)
from ...translate import _

AI_TOKEN_USD_RATE = 0.02


def _format_evaluate_quote_cost(token_count: int, *, is_ibm: bool = True) -> str:
    return format_slack_usd(token_count * AI_TOKEN_USD_RATE)


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
    ]
    # IBM Direct Login is not offered on the home tab; channel login prompts keep SSO.
    if is_ibm:
        return blocks

    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": _("Connect to Verify"),
                    },
                    "style": "primary",
                    "action_id": "login",
                    "url": get_language_cloud_connect_url(
                        user_id, team_id, enterprise_id, channel_id or user_id
                    ),
                }
            ],
        }
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
            target_lang = lang.label
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
    source_lang = quote.sl.label
    service_tra = quote.service
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


def _format_quality_discount_text(discount: dict[str, Any] | None) -> str | None:
    if not discount:
        return None
    try:
        discount_rate = float(discount.get("word_discount_rate") or 0)
    except (TypeError, ValueError):
        return None
    if discount_rate <= 0:
        return None

    tier = str(discount.get("tier") or "quality").replace("_", " ").lower()
    if discount.get("is_estimate"):
        percentage = round(discount_rate * 100)
        return f"Worst-case QE discount: -{percentage}% off"
    return f"Quality: {tier}"


def _quality_discount_savings(discount: dict[str, Any] | None) -> float:
    if not discount or discount.get("pricing_cap_applied"):
        return 0.0
    try:
        savings = float(discount.get("savings") or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(savings, 0.0)


def _target_additional_costs(
    additional_costs: list[dict[str, Any]] | None,
    *,
    file_uuid: str,
    language_uuid: str,
) -> list[dict[str, Any]]:
    return [
        additional_cost
        for additional_cost in additional_costs or []
        if additional_cost.get("file_uuid") == file_uuid
        and additional_cost.get("language_uuid") == language_uuid
    ]


def _global_additional_costs(
    additional_costs: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    return [
        additional_cost
        for additional_cost in additional_costs or []
        if not additional_cost.get("file_uuid")
        and not additional_cost.get("language_uuid")
    ]


def _target_qe_cost_total(
    additional_costs: list[dict[str, Any]] | None,
    costs: list[dict[str, Any]],
) -> float:
    """Sum per-target QE shares distributed across priced file/language rows."""
    total = 0.0
    for cost_item in costs:
        target_costs = _target_additional_costs(
            additional_costs,
            file_uuid=cost_item["file_uuid"],
            language_uuid=cost_item["language_uuid"],
        )
        total += sum(
            float(additional_cost.get("cost", 0.0) or 0.0)
            for additional_cost in target_costs
        )
    return total


def _active_quote_cost_items(
    costs: list[dict[str, Any]], job: dict[str, Any]
) -> list[dict[str, Any]]:
    """Pricing rows that remain billable after Adjust Request deselection."""
    active: list[dict[str, Any]] = []
    for item in costs:
        target_file = None
        for source_file in job.get("source_files", []):
            if source_file.get("file_uuid") != item.get("file_uuid"):
                continue
            target_file = next(
                (
                    target
                    for target in source_file.get("target_files", [])
                    if target.get("language_uuid") == item.get("language_uuid")
                ),
                None,
            )
            break
        if (target_file or {}).get("human_job_status") == "Cancelled":
            continue
        active.append(item)
    return active


def combined_quote_net_savings(
    costs: list[dict[str, Any]],
    additional_costs: list[dict[str, Any]] | None,
    *,
    show_savings: bool,
) -> float:
    """HT savings minus aggregate QE cost; zero when the bundle net benefit is non-positive."""
    if not show_savings:
        return 0.0
    total_savings = 0.0
    for item in costs:
        service = item["service_list"][0]
        total_savings += _quality_discount_savings(service.get("quality_discount"))
    return max(total_savings - _target_qe_cost_total(additional_costs, costs), 0.0)


def verify_quote_blocks(
    job: dict[str, Any],
    costs: list[dict[str, Any]],
    selectable: bool = True,
    additional_costs: list[dict[str, Any]] | None = None,
    *,
    show_quality_discount: bool = False,
    show_savings: bool = True,
    embed_additional_costs_in_line_price: bool = False,
    total_cost_label: str | None = None,
    show_submitted_costs: bool | None = None,
    show_total_cost: bool = True,
    show_estimated_completion: bool = True,
    show_evaluation_report: bool | None = None,
):
    source_files = job["source_files"]
    workflow_uuid = job["workflow_uuid"]
    if show_submitted_costs is None:
        show_submitted_costs = show_quality_discount
    # HT Adjust/quotes should not show QE Summary/Overall Score blocks.
    # Default keeps the legacy QE "Send for HV" modal behaviour.
    include_evaluation_report = (
        show_evaluation_report
        if show_evaluation_report is not None
        else workflow_uuid != HUMAN_EVALUATION_WORKFLOW_UUID
    )
    blocks: list[dict[str, Any]] = []
    total_cost = 0.0
    total_savings = 0.0
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
            quality_discount_text = None
            quality_discount_savings = 0.0
            matched_cost_item = False
            target_additional_costs = _target_additional_costs(
                additional_costs,
                file_uuid=file["file_uuid"],
                language_uuid=lang["uuid"],
            )
            target_additional_cost_total = sum(
                float(additional_cost.get("cost", 0.0) or 0.0)
                for additional_cost in target_additional_costs
            )
            for item in costs:
                if (
                    item["language_uuid"] == lang["uuid"]
                    and item["file_uuid"] == file["file_uuid"]
                ):
                    service = item["service_list"][0]
                    cost = service["estimated_cost"]
                    estimated_time = service["time_estimate_days"]
                    matched_cost_item = True
                    if show_quality_discount:
                        quality_discount_text = _format_quality_discount_text(
                            service.get("quality_discount")
                        )
                    if show_savings:
                        quality_discount_savings = _quality_discount_savings(
                            service.get("quality_discount")
                        )
                    break
            # Hide file/language pairs outside the active scope (no cost row and
            # no Cancelled/Submitted status). Without this, asymmetric per-file
            # selections render as phantom USD 0.00 lines on non-selectable quotes.
            if not matched_cost_item and not (
                target_file and target_file.get("human_job_status")
            ):
                continue
            if target_file and target_file.get("human_job_status", ""):
                if target_file["human_job_status"] == "Submitted":
                    if not selectable and show_submitted_costs:
                        line_cost = cost + target_additional_cost_total
                        total_cost += line_cost
                        total_savings += quality_discount_savings
                        quote_text = f"*{lang['name']}*\n>{format_slack_usd(line_cost)}"
                        if quality_discount_text:
                            quote_text += f"\n>{quality_discount_text}"
                        blocks.append(
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": quote_text,
                                },
                            }
                        )
                    else:
                        lang_label = f"*{lang['name']}*"
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
                            total_cost += cost + target_additional_cost_total
                            total_savings += quality_discount_savings
                        blocks.append(cost_block)
                elif target_file["human_job_status"] == "Cancelled":
                    lang_label = f"*{lang['name']}*"
                    blocks.append(
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": _("{lang_label}\n>Cancelled"),
                            },
                        }
                    )
            else:
                line_cost = cost + target_additional_cost_total
                total_cost += line_cost
                total_savings += quality_discount_savings
                report = None
                if include_evaluation_report:
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
                    option_text = f"*{lang['name']}*: {format_slack_usd(line_cost)}"
                    if quality_discount_text:
                        option_text += f"\n{quality_discount_text}"
                    if not embed_additional_costs_in_line_price:
                        for additional_cost in target_additional_costs:
                            label = additional_cost.get("label", "")
                            amount = float(additional_cost.get("cost", 0.0) or 0.0)
                            option_text += f"\n{label}: {format_slack_usd(amount)}"
                    embedded_qe_value = (
                        0.0
                        if embed_additional_costs_in_line_price
                        else target_additional_cost_total
                    )
                    option_value = (
                        f"{file['file_uuid']}:{lang['uuid']}:{estimated_time}:"
                        f"{quality_discount_savings:.2f}:"
                        f"{embedded_qe_value:.2f}"
                    )
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
                                                "text": option_text,
                                            },
                                            "value": option_value,
                                        },
                                    ],
                                    "initial_options": [
                                        {
                                            "text": {
                                                "type": "mrkdwn",
                                                "text": option_text,
                                            },
                                            "value": option_value,
                                        },
                                    ],
                                    "action_id": "verification_checkbox_action",
                                },
                            ],
                        }
                    )
                    if include_evaluation_report:
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
                                            "text": _(
                                                "*Overall Score:*\n{report_message}"
                                            ),
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
                    quote_text = f"*{lang['name']}*\n>{format_slack_usd(line_cost)}"
                    if quality_discount_text:
                        quote_text += f"\n>{quality_discount_text}"
                    if not embed_additional_costs_in_line_price:
                        for additional_cost in target_additional_costs:
                            label = additional_cost.get("label", "")
                            amount = float(additional_cost.get("cost", 0.0) or 0.0)
                            quote_text += f"\n>{label}: {format_slack_usd(amount)}"
                    blocks.append(
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": quote_text,
                            },
                        }
                    )
        blocks.append({"type": "divider"})
    active_cost_items = _active_quote_cost_items(costs, job)
    for additional_cost in _global_additional_costs(additional_costs):
        label = additional_cost.get("label", "")
        amount = float(additional_cost.get("cost", 0.0) or 0.0)
        total_cost += amount
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{label}*: {format_slack_usd(amount)}",
                },
            }
        )
        blocks.append({"type": "divider"})
    # Verify-aligned: max turnaround across selected file/language pairs
    total_estimated_days = calculate_total_estimated_days(
        [
            cost_item["service_list"][0]["time_estimate_days"]
            for cost_item in active_cost_items
        ]
    )

    # Calculate completion date
    completion_date = datetime.now() + timedelta(days=total_estimated_days)
    formatted_date = completion_date.strftime("%d %B %Y")

    # Pre-QE / Adjust Request: Maximum Total Cost; post-QE: Final Cost.
    # Translate the label (and optional savings clause) as literals — never wrap the
    # assembled cost line in _(), or Translator looks up dynamic USD amounts as labels.
    if total_cost_label is None:
        total_cost_label = (
            _("Maximum Total Cost") if not show_savings else _("Total Cost")
        )
    else:
        total_cost_label = _(total_cost_label)
    formatted_total = format_slack_usd(total_cost)
    total_cost_text = f"*{total_cost_label}*: {formatted_total}"
    if show_savings:
        displayed_savings = (
            combined_quote_net_savings(
                active_cost_items,
                additional_costs,
                show_savings=True,
            )
            if embed_additional_costs_in_line_price
            else total_savings
        )
        if displayed_savings > 0:
            formatted_savings = format_slack_usd(displayed_savings)
            total_cost_text += f" {_('(saved {formatted_savings})')}"

    if show_total_cost:
        blocks.append(
            {
                "type": "section",
                "block_id": "total_cost_block",
                "text": {
                    "type": "mrkdwn",
                    "text": total_cost_text,
                },
            }
        )
    if show_estimated_completion:
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


def document_mt_quote_blocks(
    session: dict[str, Any],
    *,
    actions: bool = True,
    status_message: str | None = None,
) -> list[dict[str, Any]]:
    """Build document MT quote blocks using the shared evaluate service quote layout."""
    quote = session.get("quote") or {}
    pdf_tokens = int(quote.get("pdf_conversion_tokens") or 0)
    total_tokens = int(quote.get("total_tokens") or 0)
    translation_tokens = max(total_tokens - pdf_tokens, 0)
    pdf_page_count = sum(
        int(file.get("pdf_conversion_page_count") or 0)
        for file in quote.get("files") or []
    )

    return evaluation_credits_quote_blocks(
        _("AI Translation"),
        translation_tokens,
        pdf_page_count=pdf_page_count or None,
        pdf_tokens=pdf_tokens or None,
        accept_action_id="document_mt_quote_accept",
        job_uuid=str(session["quote_id"]),
        actions=actions,
        status_message=status_message,
        is_ibm=is_ibm_enterprise(session.get("enterprise_id")),
    )


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
                lang_label = f"*{lang['name']}*\n"
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
    formatted_source_lang = source_lang["name"]
    formatted_target_lang = lang["name"]
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
    formatted_target_lang = lang["name"]
    file_name = file["filename"]
    return _("Translate to: {formatted_target_lang}\nFile Uploaded: {file_name}\n")


def evaluation_credits_quote_blocks(
    service_label: str,
    token_cost: int,
    *,
    pdf_page_count: int | None = None,
    pdf_tokens: int | None = None,
    accept_action_id: str,
    adjust_action_id: str | None = None,
    job_uuid: str,
    actions: bool = True,
    status_message: str | None = None,
    download_translations_job_uuid: str | None = None,
    is_ibm: bool = False,
    language_costs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build Slack blocks for a single-service evaluate credits quote."""
    cost_label = _("Cost")
    total_label = _("Total cost")
    # Staged evaluate (HT) quotes expose Adjust Request; Document MT does not.
    intro_text = (
        _("AI pre-translation before human review will incur the following cost:")
        if adjust_action_id
        else _("Running the AI translation will incur the following cost:")
    )
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": _("Service Quote"), "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": intro_text,
            },
        },
        {"type": "divider"},
    ]
    # PDF conversion runs first in the workflow, so list it above AI Translation.
    total_tokens = token_cost
    if pdf_tokens and pdf_page_count:
        blocks.append(
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"*{_('PDF conversion')}:*\n{pdf_page_count} {_('pages')}"
                        ),
                    },
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"*{cost_label}:*\n"
                            f"{_format_evaluate_quote_cost(pdf_tokens, is_ibm=is_ibm)}"
                        ),
                    },
                ],
            }
        )
        total_tokens += pdf_tokens
    if language_costs:
        current_file: str | None = None
        # Always label the AI Translation section (matches PDF conversion header).
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{service_label}:*"},
            }
        )
        for language_cost in language_costs:
            file_label = str(language_cost.get("file_label") or "")
            if file_label and file_label != current_file:
                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f":paperclip: *{file_label}*",
                        },
                    }
                )
                current_file = file_label
            if language_cost.get("cancelled"):
                line_text = f"*{language_cost['label']}*\n>{_('Cancelled')}"
            else:
                line_text = (
                    f"*{language_cost['label']}*\n>"
                    f"{_format_evaluate_quote_cost(int(language_cost['token']), is_ibm=is_ibm)}"
                )
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": line_text,
                    },
                }
            )
    else:
        blocks.append(
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*{_('Service')}:*\n{service_label}"},
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"*{cost_label}:*\n"
                            f"{_format_evaluate_quote_cost(token_cost, is_ibm=is_ibm)}"
                        ),
                    },
                ],
            }
        )
    blocks.extend(
        [
            {"type": "divider"},
            *(
                [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": _(
                                "Review the cost below and click *Accept Quote* to "
                                "continue, or *Adjust Request* to remove languages "
                                "and/or source files"
                            ),
                        },
                    }
                ]
                if actions and adjust_action_id
                else []
            ),
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{total_label}:* "
                        f"{_format_evaluate_quote_cost(total_tokens, is_ibm=is_ibm)}"
                    ),
                },
            },
        ]
    )
    if status_message:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": status_message},
            }
        )
    if actions or download_translations_job_uuid:
        elements: list[dict[str, Any]] = []
        if actions:
            if adjust_action_id:
                elements.append(
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Adjust Request"),
                        },
                        "value": job_uuid,
                        "action_id": adjust_action_id,
                    }
                )
            elements.append(
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": _("Accept Quote"),
                    },
                    "style": "primary",
                    "value": job_uuid,
                    "action_id": accept_action_id,
                }
            )
        if download_translations_job_uuid:
            elements.append(
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": _("Download AI Translations"),
                    },
                    "value": download_translations_job_uuid,
                    "action_id": "download_ai_translations_action",
                }
            )
        blocks.append({"type": "actions", "elements": elements})
    return blocks


def evaluate_ai_only_download_blocks(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Download-only blocks for AI translation without quality evaluation."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _(
                    "Your AI translation is ready. Download the translated file(s) below. "
                    "You can still accept the Quality Evaluation quote above if you want scoring."
                ),
            },
        }
    ]
    all_langs = get_languages_sync()
    for file in job.get("source_files", []):
        for lang in job.get("target_languages", []):
            lang_data = next(
                (
                    language
                    for language in all_langs
                    if language["uuid"] == lang["uuid"]
                ),
                None,
            )
            if not lang_data:
                continue
            target_file_uuid = ""
            for target_file in file.get("target_files", []):
                if target_file["language_uuid"] == lang["uuid"]:
                    target_file_uuid = target_file.get("target_file_uuid", "")
                    break
            if not target_file_uuid:
                continue
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{_('File')}:* {file.get('filename', '')}\n"
                        f"*{_('Target language')}:* {lang_data['name']}",
                    },
                }
            )
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
                }
            )
    return blocks
