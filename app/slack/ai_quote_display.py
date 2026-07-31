"""Display helpers for AI Translation quote line amounts.

Per-target token rows are individually ceiled, so summing them can exceed the
aggregate charged AI total (minimum-token cases). Quote UIs distribute the
charged aggregate across active rows so line amounts always sum to Total.
USD display uses two decimal places (see ``format_slack_usd``).
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from app.slack.media_quotes import AI_TOKEN_USD_RATE


def distribute_usd_by_weights(total_usd: float, weights: list[int]) -> list[float]:
    """Split ``total_usd`` across ``weights`` using largest remainder on cents."""
    if not weights:
        return []
    total_cents = int(round(float(total_usd) * 100))
    if total_cents <= 0:
        return [0.0] * len(weights)
    normalized = [max(int(weight), 0) for weight in weights]
    weight_sum = sum(normalized)
    if weight_sum <= 0:
        base, remainder = divmod(total_cents, len(weights))
        return [
            (base + (1 if index < remainder else 0)) / 100.0
            for index in range(len(weights))
        ]
    raw = [total_cents * weight / weight_sum for weight in normalized]
    floors = [math.floor(value) for value in raw]
    remainder = total_cents - sum(floors)
    order = sorted(
        range(len(weights)),
        key=lambda index: (raw[index] - floors[index], -index),
        reverse=True,
    )
    for index in order[:remainder]:
        floors[index] += 1
    return [cents / 100.0 for cents in floors]


def _pair_key(row: dict[str, Any]) -> str:
    file_uuid = str(row.get("file_uuid") or "")
    language = str(row.get("value") or "")
    if file_uuid and language:
        return f"{file_uuid}:{language}"
    return language


def ai_language_cost_display_amounts(
    aggregate_tokens: int,
    language_costs: Iterable[dict[str, Any]],
    *,
    selected_pairs: Iterable[str] | None = None,
    rate: float = AI_TOKEN_USD_RATE,
) -> list[float | None]:
    """Return per-row USD amounts that sum to the aggregate AI charge.

    Cancelled rows (or rows outside ``selected_pairs`` when provided) return
    ``None`` so callers can render a cancelled label instead of a price.
    Unselected modal rows return ``0.0``.
    """
    rows = list(language_costs)
    selected = (
        {str(pair) for pair in selected_pairs} if selected_pairs is not None else None
    )
    amounts: list[float | None] = [None] * len(rows)
    active_indices: list[int] = []
    weights: list[int] = []
    for index, row in enumerate(rows):
        if row.get("cancelled"):
            continue
        key = _pair_key(row)
        if selected is not None and key not in selected:
            amounts[index] = 0.0
            continue
        active_indices.append(index)
        weights.append(int(row.get("token") or 0))
    if not active_indices:
        return amounts
    distributed = distribute_usd_by_weights(float(aggregate_tokens) * rate, weights)
    for index, amount in zip(active_indices, distributed, strict=True):
        amounts[index] = amount
    return amounts
