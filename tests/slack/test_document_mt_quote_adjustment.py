"""Tests for the Document MT quote Adjust Request flow (RAY-79115)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.slack.document_mt_quote_adjustment import (
    document_mt_all_pairs,
    document_mt_filter_rows,
    document_mt_language_costs,
    document_mt_language_costs_with_cancelled,
    document_mt_pdf_pages_for_pairs,
    document_mt_pdf_tokens_for_pairs,
    document_mt_tokens_for_pairs,
)
from app.slack.evaluation_ai_quote_modal_service import (
    populate_ai_quote_adjustment_modal,
    refresh_ai_quote_adjustment_cost,
)
from app.slack.evaluation_ai_quote_submit_service import persist_ai_quote_adjustment


def _quote():
    return {
        "currency": "USD",
        "total_tokens": 900,
        "pdf_conversion_tokens": 100,
        "total_cost_usd": 18.00,
        "files": [
            {
                "file_id": "grid-1",
                "file_name": "document.docx",
                # 150000 chars → ceil(150000×0.002)=300 per lang; ×2 langs → 600.
                "character_count": 150000,
                "pdf_conversion_page_count": None,
                "pdf_conversion_tokens": 0,
                "target_languages": [
                    {"target_language": "fr", "tokens": 300, "cost_usd": 6.00},
                    {"target_language": "de", "tokens": 300, "cost_usd": 6.00},
                ],
            },
            {
                "file_id": "grid-2",
                "file_name": "legal-appendix.pdf",
                # 100000 chars → ceil(100000×0.002)=200.
                "character_count": 100000,
                "pdf_conversion_page_count": 4,
                "pdf_conversion_tokens": 100,
                "target_languages": [
                    {"target_language": "fr", "tokens": 200, "cost_usd": 4.00},
                ],
            },
        ],
    }


def _session(**overrides):
    session = {
        "quote_id": "quote-1",
        "user_id": "U1",
        "team_id": "T1",
        "enterprise_id": None,
        "channel_id": "C1",
        "status": "quoted",
        "quote": _quote(),
    }
    session.update(overrides)
    return session


class TestDocumentMtQuoteAdjustmentHelpers:
    def test_language_costs_builds_rows_with_display_names(self):
        rows = document_mt_language_costs(_quote())

        assert [
            (row["file_uuid"], row["value"], row["label"], row["token"]) for row in rows
        ] == [
            ("grid-1", "fr", "French", 300),
            ("grid-1", "de", "German", 300),
            ("grid-2", "fr", "French", 200),
        ]
        assert rows[0]["file_label"] == "document.docx"
        assert rows[2]["file_label"] == "legal-appendix.pdf"

    def test_language_costs_unknown_code_falls_back_to_code(self):
        quote = _quote()
        quote["files"][0]["target_languages"][0]["target_language"] = "xx-custom"

        rows = document_mt_language_costs(quote, language_names={})

        assert rows[0]["label"] == "xx-custom"

    def test_all_pairs(self):
        assert document_mt_all_pairs(_quote()) == [
            "grid-1:de",
            "grid-1:fr",
            "grid-2:fr",
        ]

    def test_filter_rows_by_pairs(self):
        rows = document_mt_language_costs(_quote())

        filtered = document_mt_filter_rows(rows, ["grid-1:fr"])

        assert [(row["file_uuid"], row["value"]) for row in filtered] == [
            ("grid-1", "fr")
        ]

    def test_language_costs_with_cancelled_marks_deselected(self):
        rows = document_mt_language_costs(_quote())

        marked = document_mt_language_costs_with_cancelled(rows, ["grid-1:fr"])

        by_key = {
            f"{row['file_uuid']}:{row['value']}": row["cancelled"] for row in marked
        }
        assert by_key == {
            "grid-1:fr": False,
            "grid-1:de": True,
            "grid-2:fr": True,
        }
        assert len(marked) == 3

    def test_language_costs_with_cancelled_marks_all_when_empty(self):
        rows = document_mt_language_costs(_quote())

        marked = document_mt_language_costs_with_cancelled(rows, [])

        assert marked
        assert all(row["cancelled"] is True for row in marked)

    def test_tokens_for_pairs_uses_sow_for_selected_count(self):
        # One lang each file: ceil(150000×0.002)+ceil(100000×0.002)=300+200.
        assert document_mt_tokens_for_pairs(_quote(), ["grid-1:fr", "grid-2:fr"]) == 500

    def test_tokens_for_pairs_full_selection_uses_sow_not_row_sum(self):
        quote = {
            "total_tokens": 26,
            "pdf_conversion_tokens": 25,
            "files": [
                {
                    "file_id": "grid-1",
                    "file_name": "brief.pdf",
                    "character_count": 100,
                    "pdf_conversion_page_count": 1,
                    "pdf_conversion_tokens": 25,
                    "target_languages": [
                        {"target_language": "hr", "tokens": 1, "cost_usd": 0.02},
                        {"target_language": "ny", "tokens": 1, "cost_usd": 0.02},
                    ],
                }
            ],
        }
        # Row sum would be 2; SOW ceil(100×2×0.002)=1.
        assert document_mt_tokens_for_pairs(quote, ["grid-1:hr", "grid-1:ny"]) == 1
        assert document_mt_tokens_for_pairs(quote, ["grid-1:hr"]) == 1

    def test_tokens_for_pairs_discounts_exact_memory_matches(self):
        quote = {
            "total_tokens": 4,
            "files": [
                {
                    "file_id": "grid-1",
                    "file_name": "doc.docx",
                    "character_count": 1000,
                    "target_languages": [
                        {
                            "target_language": "fr",
                            "tokens": 0,
                            "cost_usd": 0.0,
                            "memory_matched_characters": 1000,
                        },
                        {
                            "target_language": "de",
                            "tokens": 2,
                            "cost_usd": 0.04,
                            "memory_matched_characters": 400,
                        },
                    ],
                }
            ],
        }
        # fr fully matched → free; de bills 1000-400=600 → ceil(600×0.002)=2.
        assert document_mt_tokens_for_pairs(quote, ["grid-1:fr", "grid-1:de"]) == 2
        assert document_mt_tokens_for_pairs(quote, ["grid-1:fr"]) == 0
        assert document_mt_tokens_for_pairs(quote, ["grid-1:de"]) == 2

    def test_tokens_for_pairs_missing_match_field_charges_full_volume(self):
        quote = {
            "total_tokens": 4,
            "files": [
                {
                    "file_id": "grid-1",
                    "file_name": "doc.docx",
                    "character_count": 1000,
                    "target_languages": [
                        {"target_language": "fr", "tokens": 2, "cost_usd": 0.04},
                    ],
                }
            ],
        }
        assert document_mt_tokens_for_pairs(quote, ["grid-1:fr"]) == 2

    def test_pdf_tokens_and_pages_follow_selected_files(self):
        # grid-2 still selected -> its PDF fee applies.
        assert document_mt_pdf_tokens_for_pairs(_quote(), ["grid-2:fr"]) == 100
        assert document_mt_pdf_pages_for_pairs(_quote(), ["grid-2:fr"]) == 4
        # grid-2 fully deselected -> no PDF fee.
        assert document_mt_pdf_tokens_for_pairs(_quote(), ["grid-1:fr"]) == 0
        assert document_mt_pdf_pages_for_pairs(_quote(), ["grid-1:fr"]) == 0


@pytest.mark.asyncio
class TestPopulateDocumentMtQuoteAdjustmentModal:
    async def test_rejects_other_users(self):
        client = AsyncMock()
        with (
            patch(
                "app.slack.evaluation_ai_quote_modal_service.get_document_mt_quote_session",
                new_callable=AsyncMock,
                return_value=_session(user_id="U_OWNER"),
            ),
            patch(
                "app.slack.evaluation_ai_quote_modal_service.safe_views_update",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            await populate_ai_quote_adjustment_modal(
                client,
                view_id="view-1",
                quote_id="quote-1",
                quote_kind="document_mt",
                user_id="U1",
                context={},
            )

        view = mock_update.await_args.args[2]
        assert "checkboxes" not in str(view)

    async def test_rejects_unquoted_session(self):
        client = AsyncMock()
        with (
            patch(
                "app.slack.evaluation_ai_quote_modal_service.get_document_mt_quote_session",
                new_callable=AsyncMock,
                return_value=_session(status="pending"),
            ),
            patch(
                "app.slack.evaluation_ai_quote_modal_service.safe_views_update",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            await populate_ai_quote_adjustment_modal(
                client,
                view_id="view-1",
                quote_id="quote-1",
                quote_kind="document_mt",
                user_id="U1",
                context={},
            )

        view = mock_update.await_args.args[2]
        assert "checkboxes" not in str(view)

    async def test_populates_checkbox_modal_from_quote_session(self):
        client = AsyncMock()
        with (
            patch(
                "app.slack.evaluation_ai_quote_modal_service.get_document_mt_quote_session",
                new_callable=AsyncMock,
                return_value=_session(),
            ),
            patch(
                "app.slack.evaluation_ai_quote_modal_service.update_document_mt_quote_session",
                new_callable=AsyncMock,
            ) as mock_update_session,
            patch(
                "app.slack.evaluation_ai_quote_modal_service.safe_views_update",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            await populate_ai_quote_adjustment_modal(
                client,
                view_id="view-1",
                quote_id="quote-1",
                quote_kind="document_mt",
                user_id="U1",
                context={"channel_id": "C1"},
                channel_id="C1",
                message_ts="111.222",
            )

        modal = mock_update.await_args.args[2]
        assert modal["callback_id"] == "evaluation_ai_quote_adjust_submit"
        metadata = json.loads(modal["private_metadata"])
        assert metadata["quote_kind"] == "document_mt"
        assert metadata["message_ts"] == "111.222"
        rendered = str(modal["blocks"])
        assert "document.docx" in rendered
        assert "legal-appendix.pdf" in rendered
        assert "French" in rendered
        assert "German" in rendered
        # All rows checked by default; full grid total = 900 tokens -> USD 18.00.
        assert rendered.count("initial_options") == 3
        assert "USD 18.00" in rendered
        mock_update_session.assert_awaited_once_with(
            "quote-1", {"message_ts": "111.222"}
        )


@pytest.mark.asyncio
class TestRefreshDocumentMtQuoteAdjustmentCost:
    async def test_refresh_recomputes_totals_from_selection(self):
        view = {
            "id": "view-1",
            "state": {
                "values": {
                    "ai_quote_language_grid-1_fr": {
                        "evaluation_ai_quote_language_selection": {
                            "selected_options": [{"value": "grid-1:fr"}],
                        }
                    },
                }
            },
            "blocks": [
                {
                    "block_id": "total_cost_block",
                    "text": {"type": "mrkdwn", "text": "*Total cost:* USD 18.00"},
                }
            ],
        }
        client = AsyncMock()
        with patch(
            "app.slack.evaluation_ai_quote_modal_service.get_document_mt_quote_session",
            new_callable=AsyncMock,
            return_value=_session(),
        ):
            await refresh_ai_quote_adjustment_cost(
                client,
                view=view,
                quote_id="quote-1",
                quote_kind="document_mt",
            )

        updated_view = client.views_update.await_args.kwargs["view"]
        # grid-1 French row only: 300 tokens -> USD 6.00, PDF fee dropped.
        assert "USD 6.00" in str(updated_view["blocks"])
        assert "USD 18.00" not in str(updated_view["blocks"])

    async def test_refresh_redistributes_minimum_ai_charge_across_selected(self):
        session = _session()
        session["quote"] = {
            "currency": "USD",
            "total_tokens": 26,
            "pdf_conversion_tokens": 25,
            "total_cost_usd": 0.52,
            "files": [
                {
                    "file_id": "grid-1",
                    "file_name": "brief.pdf",
                    "character_count": 100,
                    "pdf_conversion_page_count": 1,
                    "pdf_conversion_tokens": 25,
                    "target_languages": [
                        {"target_language": "hr", "tokens": 1, "cost_usd": 0.02},
                        {"target_language": "ny", "tokens": 1, "cost_usd": 0.02},
                    ],
                }
            ],
        }
        hr_option = {
            "text": {"type": "mrkdwn", "text": "*Croatian*: USD 0.02"},
            "value": "grid-1:hr",
        }
        ny_option = {
            "text": {"type": "mrkdwn", "text": "*Chichewa*: USD 0.02"},
            "value": "grid-1:ny",
        }
        view = {
            "id": "view-1",
            "state": {
                "values": {
                    "ai_quote_language_grid-1_hr": {
                        "evaluation_ai_quote_language_selection": {
                            "selected_options": [hr_option],
                        }
                    },
                    "ai_quote_language_grid-1_ny": {
                        "evaluation_ai_quote_language_selection": {
                            "selected_options": [ny_option],
                        }
                    },
                }
            },
            "blocks": [
                {
                    "type": "actions",
                    "block_id": "ai_quote_language_grid-1_hr",
                    "elements": [
                        {
                            "type": "checkboxes",
                            "action_id": "evaluation_ai_quote_language_selection",
                            "options": [hr_option],
                            "initial_options": [hr_option],
                        }
                    ],
                },
                {
                    "type": "actions",
                    "block_id": "ai_quote_language_grid-1_ny",
                    "elements": [
                        {
                            "type": "checkboxes",
                            "action_id": "evaluation_ai_quote_language_selection",
                            "options": [ny_option],
                            "initial_options": [ny_option],
                        }
                    ],
                },
                {
                    "type": "section",
                    "block_id": "ai_quote_pdf_cost_block",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*PDF conversion:* USD 0.50",
                    },
                },
                {
                    "type": "section",
                    "block_id": "total_cost_block",
                    "text": {"type": "mrkdwn", "text": "*Total cost:* USD 0.54"},
                },
            ],
        }
        client = AsyncMock()
        with patch(
            "app.slack.evaluation_ai_quote_modal_service.get_document_mt_quote_session",
            new_callable=AsyncMock,
            return_value=session,
        ):
            await refresh_ai_quote_adjustment_cost(
                client,
                view=view,
                quote_id="quote-1",
                quote_kind="document_mt",
            )

        updated = client.views_update.await_args.kwargs["view"]
        option_labels = [
            option["text"]["text"]
            for block in updated["blocks"]
            for element in block.get("elements") or []
            for option in element.get("options") or []
        ]
        # SOW AI total is 1 token ($0.02), split across both checked rows.
        assert option_labels == [
            "*Croatian*: USD 0.01",
            "*Chichewa*: USD 0.01",
        ]
        assert "*Total cost:* USD 0.52" in str(updated["blocks"])
        assert "USD 0.54" not in str(updated["blocks"])


@pytest.mark.asyncio
class TestPersistDocumentMtQuoteAdjustment:
    async def test_rejects_wrong_status(self):
        with patch(
            "app.slack.evaluation_ai_quote_submit_service.get_document_mt_quote_session",
            new_callable=AsyncMock,
            return_value=_session(status="accepted"),
        ):
            persisted = await persist_ai_quote_adjustment(
                AsyncMock(),
                quote_id="quote-1",
                quote_kind="document_mt",
                selected_pairs=["grid-1:fr"],
                user_id="U1",
                context={},
            )

        assert persisted is False

    async def test_persists_pairs_and_refreshes_quote_message(self):
        client = AsyncMock()
        updated_session = _session(selected_pairs=["grid-1:fr"])
        with (
            patch(
                "app.slack.evaluation_ai_quote_submit_service.get_document_mt_quote_session",
                new_callable=AsyncMock,
                return_value=_session(),
            ),
            patch(
                "app.slack.evaluation_ai_quote_submit_service.update_document_mt_quote_session",
                new_callable=AsyncMock,
                return_value=updated_session,
            ) as mock_update_session,
            patch(
                "app.slack.evaluation_ai_quote_submit_service.update_document_mt_quote_slack_message",
                new_callable=AsyncMock,
            ) as mock_update_message,
        ):
            persisted = await persist_ai_quote_adjustment(
                client,
                quote_id="quote-1",
                quote_kind="document_mt",
                selected_pairs=["grid-1:fr"],
                user_id="U1",
                context=MagicMock(),
                channel_id="C1",
                message_ts="111.222",
            )

        assert persisted is True
        mock_update_session.assert_awaited_once_with(
            "quote-1",
            {
                "selected_pairs": ["grid-1:fr"],
                "channel_id": "C1",
                "message_ts": "111.222",
            },
        )
        mock_update_message.assert_awaited_once_with(
            client,
            channel_id="C1",
            message_ts="111.222",
            session=updated_session,
            actions=True,
            status_message=None,
        )

    async def test_empty_selection_cancels_quote_message(self):
        client = AsyncMock()
        updated_session = _session(selected_pairs=[], status="cancelled")
        with (
            patch(
                "app.slack.evaluation_ai_quote_submit_service.get_document_mt_quote_session",
                new_callable=AsyncMock,
                return_value=_session(),
            ),
            patch(
                "app.slack.evaluation_ai_quote_submit_service.update_document_mt_quote_session",
                new_callable=AsyncMock,
                return_value=updated_session,
            ) as mock_update_session,
            patch(
                "app.slack.evaluation_ai_quote_submit_service.update_document_mt_quote_slack_message",
                new_callable=AsyncMock,
            ) as mock_update_message,
        ):
            persisted = await persist_ai_quote_adjustment(
                client,
                quote_id="quote-1",
                quote_kind="document_mt",
                selected_pairs=[],
                user_id="U1",
                context=MagicMock(),
                channel_id="C1",
                message_ts="111.222",
            )

        assert persisted is True
        # Session is atomically marked cancelled so a concurrent Accept cannot
        # bill the full batch while the Slack message is refreshed.
        assert mock_update_session.await_args.args[1]["status"] == "cancelled"
        mock_update_message.assert_awaited_once()
        assert mock_update_message.await_args.kwargs["actions"] is False
        assert (
            "cancelled"
            in str(mock_update_message.await_args.kwargs["status_message"]).lower()
        )


@pytest.mark.asyncio
class TestPopulateEvaluateAiQuoteAdjustmentModal:
    async def test_uses_frozen_snapshot_amounts_without_requote(self):
        client = AsyncMock()
        session = {
            "user_id": "U1",
            "stage": "awaiting_ai",
            "channel_id": "C1",
            "message_ts": "111.222",
            "quote_snapshot": {
                "token_cost": 100,
                "ai_translation_file_and_languages": ["f1:l1"],
                "ai_quote_details": [
                    {
                        "file_uuid": "f1",
                        "target_language_uuid": "l1",
                        "token": 100,
                    }
                ],
                "all_language_costs": [
                    {
                        "file_uuid": "f1",
                        "file_label": "file.docx",
                        "value": "l1",
                        "label": "French",
                        "token": 100,
                    }
                ],
                "language_costs": [
                    {
                        "file_uuid": "f1",
                        "file_label": "file.docx",
                        "value": "l1",
                        "label": "French",
                        "token": 100,
                    }
                ],
                "file_uuids": ["f1"],
            },
        }
        with (
            patch(
                "app.slack.evaluation_ai_quote_modal_service.populate_ray_connection",
                new_callable=AsyncMock,
            ),
            patch(
                "app.slack.evaluation_ai_quote_modal_service.require_ray_client",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.slack.evaluation_ai_quote_modal_service.get_evaluate_quote_session",
                new_callable=AsyncMock,
                return_value=session,
            ),
            patch(
                "app.slack.evaluation_ai_quote_modal_service.safe_views_update",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            await populate_ai_quote_adjustment_modal(
                client,
                view_id="view-1",
                quote_id="job-1",
                quote_kind="extracted",
                user_id="U1",
                context={"channel_id": "C1"},
                channel_id="C1",
                message_ts="111.222",
            )

        modal = mock_update.await_args.args[2]
        rendered = str(modal["blocks"])
        assert "file.docx" in rendered
        assert "French" in rendered
        assert "USD 2.00" in rendered
