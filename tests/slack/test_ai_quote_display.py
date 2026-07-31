"""Tests for AI quote display USD distribution."""

from app.slack.ai_quote_display import (
    ai_language_cost_display_amounts,
    distribute_usd_by_weights,
)


def test_distribute_usd_by_weights_proportional():
    assert distribute_usd_by_weights(2.40, [40, 80]) == [0.80, 1.60]


def test_distribute_usd_by_weights_minimum_split():
    # $0.02 across two equal rows → one cent each.
    assert distribute_usd_by_weights(0.02, [1, 1]) == [0.01, 0.01]


def test_distribute_usd_by_weights_largest_remainder():
    # $0.02 across three equal rows → two cents assigned, one zero.
    assert sum(distribute_usd_by_weights(0.02, [1, 1, 1])) == 0.02
    assert sorted(distribute_usd_by_weights(0.02, [1, 1, 1])) == [0.00, 0.01, 0.01]


def test_ai_language_cost_display_amounts_skips_cancelled():
    amounts = ai_language_cost_display_amounts(
        1,
        [
            {"file_uuid": "f1", "value": "hr", "token": 1},
            {"file_uuid": "f1", "value": "ny", "token": 1, "cancelled": True},
        ],
    )
    assert amounts == [0.02, None]


def test_ai_language_cost_display_amounts_selected_pairs():
    amounts = ai_language_cost_display_amounts(
        1,
        [
            {"file_uuid": "f1", "value": "hr", "token": 1},
            {"file_uuid": "f1", "value": "ny", "token": 1},
        ],
        selected_pairs=["f1:hr"],
    )
    assert amounts == [0.02, 0.0]
