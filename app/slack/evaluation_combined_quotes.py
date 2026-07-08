"""Combined Quality Evaluation + Human Translation quote orchestration."""

from typing import Any

from slack_sdk.web.async_client import AsyncWebClient

from app.api.verify import (
    get_evaluation_job,
    get_evaluation_job_quote,
    get_job_pricing,
)
from app.auth.connector import get_ray_client
from app.constants import EVALUATE_SERVICE_QUALITY_EVALUATION
from app.dependencies import RayEvent, RayEventAuth
from app.ray.events.logging import post_notification
from app.slack.evaluation_quotes import (
    STAGE_AWAITING_QE,
    get_evaluate_quote_session,
    resolve_evaluate_channel_id,
    save_evaluate_quote_session,
)
from app.slack.templates.messages import HumanJobQuoteMessage, SlackMessage
from app.translate import _

COMBINED_QE_HUMAN_QUOTE_ACCEPT_ACTION_ID = "evaluation_qe_human_quote_accept"
WORST_CASE_QE_QUALITY_TIER = "bad"
QE_TOKEN_USD_RATE = 0.02
PRE_QE_QUOTE_DISPLAY = {
    "show_quality_discount": False,
    "show_savings": False,
    "embed_additional_costs_in_line_price": True,
    "total_cost_label": "Maximum Total Cost",
}
POST_QE_QUOTE_DISPLAY = {
    "show_quality_discount": True,
    "show_savings": True,
    "embed_additional_costs_in_line_price": True,
    "total_cost_label": "Final Cost",
}


def qe_total_usd(token_cost: int) -> float:
    return token_cost * QE_TOKEN_USD_RATE


def validate_combined_quote_includes_qe_cost(
    costs: list[dict[str, Any]],
    qe_token_cost: int,
    *,
    show_savings: bool,
    pricing_costs: list[dict[str, Any]] | None = None,
) -> dict[str, float]:
    """Ensure combined quote totals include the aggregate QE charge."""
    from app.slack.templates.blocks import (
        _quality_discount_savings,
        _target_qe_cost_total,
        combined_quote_net_savings,
    )

    priced_costs = pricing_costs if pricing_costs is not None else costs
    additional_costs = qe_additional_cost(
        qe_token_cost,
        costs,
        pricing_costs=priced_costs,
    )
    qe_total = _target_qe_cost_total(additional_costs, priced_costs)
    ht_total = sum(
        float(item["service_list"][0]["estimated_cost"]) for item in priced_costs
    )
    total_cost = ht_total + qe_total
    raw_savings = sum(
        _quality_discount_savings(item["service_list"][0].get("quality_discount"))
        for item in priced_costs
    )
    net_savings = combined_quote_net_savings(
        priced_costs,
        additional_costs,
        show_savings=show_savings,
    )
    if qe_total > 0 and total_cost < ht_total + qe_total - 0.005:
        raise ValueError(
            "Combined quote total does not include the Quality Evaluation cost"
        )
    return {
        "ht_total": ht_total,
        "qe_total": qe_total,
        "total_cost": total_cost,
        "raw_savings": raw_savings,
        "net_savings": net_savings,
    }


def combined_human_job_quote_message(
    job_data: dict[str, Any],
    costs: list[dict[str, Any]],
    *,
    qe_token_cost: int,
    actions: bool = True,
    status_message: str | None = None,
    allow_adjust: bool = True,
    download_translations_job_uuid: str | None = None,
    show_quality_discount: bool = True,
    show_savings: bool = True,
    embed_additional_costs_in_line_price: bool = False,
    total_cost_label: str | None = None,
    message_title: str | None = None,
    pricing_costs: list[dict[str, Any]] | None = None,
) -> HumanJobQuoteMessage:
    priced_costs = pricing_costs if pricing_costs is not None else costs
    return HumanJobQuoteMessage(
        job_data,
        costs,
        actions=actions,
        status_message=status_message,
        additional_costs=qe_additional_cost(
            qe_token_cost,
            costs,
            pricing_costs=priced_costs,
        ),
        accept_action_id=COMBINED_QE_HUMAN_QUOTE_ACCEPT_ACTION_ID,
        allow_adjust=allow_adjust,
        download_translations_job_uuid=download_translations_job_uuid,
        show_quality_discount=show_quality_discount,
        show_savings=show_savings,
        embed_additional_costs_in_line_price=embed_additional_costs_in_line_price,
        total_cost_label=total_cost_label,
        message_title=message_title,
    )


def qe_additional_cost(
    token_cost: int,
    costs: list[dict[str, Any]] | None = None,
    *,
    pricing_costs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    total_cost = qe_total_usd(token_cost)
    if not costs:
        return [
            {
                "label": _("Quality Evaluation"),
                "cost": total_cost,
            }
        ]

    distribution_costs = pricing_costs if pricing_costs is not None else costs
    if not distribution_costs:
        return []

    per_target_cost = total_cost / len(distribution_costs)
    return [
        {
            "label": _("Quality Evaluation"),
            "cost": per_target_cost,
            "file_uuid": cost["file_uuid"],
            "language_uuid": cost["language_uuid"],
        }
        for cost in distribution_costs
    ]


def job_file_uuids(job_data: dict[str, Any]) -> list[str]:
    return [file["file_uuid"] for file in job_data.get("source_files", [])]


def job_target_language_uuids(job_data: dict[str, Any]) -> list[str]:
    return [lang["uuid"] for lang in job_data.get("target_languages", [])]


def unsubmitted_human_translation_targets(job_data: dict[str, Any]) -> list[str]:
    file_and_languages = []
    for source_file in job_data.get("source_files", []):
        for target_file in source_file.get("target_files", []):
            if target_file.get("human_job_status"):
                continue
            file_and_languages.append(
                f"{source_file['file_uuid']}:{target_file['language_uuid']}"
            )
    return file_and_languages


def select_unsubmitted_languages(
    job_data: dict[str, Any], selected_languages: list[str] | None = None
) -> list[str]:
    selected_language_set = set(selected_languages) if selected_languages else None
    selected_languages = []
    for source_file in job_data.get("source_files", []):
        for target_file in source_file.get("target_files", []):
            if target_file.get("human_job_status"):
                continue
            file_and_language = (
                f"{source_file['file_uuid']}:{target_file['language_uuid']}"
            )
            if (
                selected_language_set is not None
                and file_and_language not in selected_language_set
            ):
                target_file["human_job_status"] = "Cancelled"
                continue
            selected_languages.append(file_and_language)
            target_file["human_job_status"] = "Submitted"
    return selected_languages


def select_all_unsubmitted_languages(job_data: dict[str, Any]) -> list[str]:
    return select_unsubmitted_languages(job_data)


def active_quote_cost_rows(
    costs: list[dict[str, Any]], selected_file_and_languages: list[str]
) -> list[dict[str, Any]]:
    selected = set(selected_file_and_languages)
    return [
        cost
        for cost in costs
        if f"{cost['file_uuid']}:{cost['language_uuid']}" in selected
    ]


def resolve_selected_human_translation_targets(
    job_data: dict[str, Any],
    *,
    selected_languages: list[str] | None = None,
) -> list[str]:
    """Return selected file/language pairs and mark deselected targets cancelled."""
    if not selected_languages:
        extra_info = job_data.get("extra_info") or {}
        stored = extra_info.get("human_translation_file_and_languages")
        if isinstance(stored, list) and stored:
            selected_languages = [str(item) for item in stored]

    if selected_languages:
        selected_set = set(selected_languages)
        applied: list[str] = []
        for source_file in job_data.get("source_files", []):
            for target_file in source_file.get("target_files", []):
                file_and_language = (
                    f"{source_file['file_uuid']}:{target_file['language_uuid']}"
                )
                if file_and_language in selected_set:
                    target_file["human_job_status"] = "Submitted"
                    applied.append(file_and_language)
                else:
                    target_file["human_job_status"] = "Cancelled"
        return applied

    return select_all_unsubmitted_languages(job_data)


def ai_translation_target_file_uuids(job_data: dict[str, Any]) -> list[str]:
    file_uuids: list[str] = []
    for source_file in job_data.get("source_files", []):
        for target_file in source_file.get("target_files", []):
            target_file_uuid = target_file.get("target_file_uuid")
            if target_file_uuid:
                file_uuids.append(str(target_file_uuid))
    return file_uuids


async def post_combined_qe_human_quote(
    client: AsyncWebClient,
    event: RayEvent,
    auth: RayEventAuth,
    *,
    job_uuid: str,
) -> None:
    if auth.slack_user is None:
        raise ValueError("Slack user is required for evaluate quote notifications")

    ray_client = await get_ray_client(
        auth.slack_user.user_id,
        auth.slack_user.team_id,
        auth.slack_user.enterprise_id,
    )
    if ray_client is None:
        raise ValueError("Could not get ray client for combined evaluate quote")

    quote = await get_evaluation_job_quote(
        ray_client,
        job_uuid,
        [EVALUATE_SERVICE_QUALITY_EVALUATION],
    )
    services_costs = quote.get("services_costs") or {}
    qe_token_cost = int(
        services_costs.get(EVALUATE_SERVICE_QUALITY_EVALUATION, quote.get("token", 0))
    )
    job = await get_evaluation_job(auth.slack_user, job_uuid)
    job_data = job["data"]
    costs = await get_job_pricing(
        ray_client,
        job_uuid,
        job_file_uuids(job_data),
        job_target_language_uuids(job_data),
        assumed_quality_tier=WORST_CASE_QE_QUALITY_TIER,
    )

    message = combined_human_job_quote_message(
        job_data,
        costs["data"],
        qe_token_cost=qe_token_cost,
        download_translations_job_uuid=job_uuid,
        show_quality_discount=PRE_QE_QUOTE_DISPLAY["show_quality_discount"],
        show_savings=PRE_QE_QUOTE_DISPLAY["show_savings"],
        embed_additional_costs_in_line_price=PRE_QE_QUOTE_DISPLAY[
            "embed_additional_costs_in_line_price"
        ],
        total_cost_label=PRE_QE_QUOTE_DISPLAY["total_cost_label"],
    )
    session = await get_evaluate_quote_session(job_uuid)
    channel_id = resolve_evaluate_channel_id(event, job_data) or auth.slack_user.user_id
    message_ts = session.get("message_ts") if session else None

    if session and session.get("channel_id"):
        channel_id = session["channel_id"]
    if message_ts:
        await client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text=message.text,
            blocks=message.blocks,
        )
    else:
        response = await post_notification(
            client,
            event,
            auth.slack_user,
            message,
            channel_id=channel_id,
        )
        message_ts = response.get("ts") if isinstance(response, dict) else None

    await save_evaluate_quote_session(
        job_uuid,
        channel_id=channel_id,
        user_id=auth.slack_user.user_id,
        team_id=auth.slack_user.team_id,
        stage=STAGE_AWAITING_QE,
        quote_snapshot={
            "service": EVALUATE_SERVICE_QUALITY_EVALUATION,
            "token_cost": qe_token_cost,
            "service_label": _("Quality Evaluation + Human Translation"),
            "accept_action_id": COMBINED_QE_HUMAN_QUOTE_ACCEPT_ACTION_ID,
            "assumed_quality_tier": WORST_CASE_QE_QUALITY_TIER,
            "auto_submit_human_job": True,
        },
        message_ts=message_ts,
    )


async def handle_combined_qe_complete(
    client: AsyncWebClient,
    event: RayEvent,
    auth: RayEventAuth,
    *,
    job_data: dict[str, Any],
    costs: list[dict[str, Any]],
) -> bool:
    """After QE scoring completes, refresh the quote and post a status message."""
    session = await get_evaluate_quote_session(job_data["uuid"])
    quote_snapshot = (session or {}).get("quote_snapshot") or {}
    if not quote_snapshot.get("auto_submit_human_job"):
        return False

    qe_token_cost = int(quote_snapshot.get("token_cost") or 0)
    channel_id = (session or {}).get("channel_id") or resolve_evaluate_channel_id(
        event, job_data
    )
    message_ts = (session or {}).get("message_ts")
    selected_languages = quote_snapshot.get("selected_languages")
    selected_targets = resolve_selected_human_translation_targets(
        job_data,
        selected_languages=selected_languages,
    )
    pricing_costs = active_quote_cost_rows(costs, selected_targets)

    quote_summary = validate_combined_quote_includes_qe_cost(
        costs,
        qe_token_cost,
        show_savings=True,
        pricing_costs=pricing_costs,
    )

    refreshed_message = combined_human_job_quote_message(
        job_data,
        costs,
        qe_token_cost=qe_token_cost,
        pricing_costs=pricing_costs,
        actions=False,
        allow_adjust=False,
        download_translations_job_uuid=job_data["uuid"],
        message_title=_("Quote"),
        show_quality_discount=POST_QE_QUOTE_DISPLAY["show_quality_discount"],
        show_savings=POST_QE_QUOTE_DISPLAY["show_savings"],
        embed_additional_costs_in_line_price=POST_QE_QUOTE_DISPLAY[
            "embed_additional_costs_in_line_price"
        ],
        total_cost_label=POST_QE_QUOTE_DISPLAY["total_cost_label"],
    )
    if channel_id and message_ts:
        await client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text=refreshed_message.text,
            blocks=refreshed_message.blocks,
        )
    elif auth.slack_user is not None:
        await post_notification(
            client,
            event,
            auth.slack_user,
            refreshed_message,
            channel_id=channel_id,
        )
    else:
        return False

    if auth.slack_user is None:
        return True

    status_message = combined_qe_complete_status_message(
        total_cost=quote_summary["total_cost"],
        net_savings=quote_summary["net_savings"],
    )
    await post_notification(
        client,
        event,
        auth.slack_user,
        status_message,
        channel_id=channel_id,
    )
    return True


def combined_qe_complete_status_message(
    *,
    total_cost: float,
    net_savings: float,
) -> SlackMessage:
    """Short follow-up after QE completes while Human Translation is submitted."""
    if net_savings > 0:
        final_cost_line = _(
            "Final cost after Arbitr evaluation: USD ${total_cost:.2f} "
            "(saved ${net_savings:.2f})"
        )
    else:
        final_cost_line = _("Final cost after Arbitr evaluation: USD ${total_cost:.2f}")

    return SlackMessage(
        final_cost_line,
        [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": final_cost_line,
                },
            },
        ],
    )
