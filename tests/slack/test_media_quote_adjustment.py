"""Tests for media Quote2 AI Translation Adjust Request (RAY-79115)."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.slack.evaluation_ai_quote_modal_service import (
    populate_ai_quote_adjustment_modal,
    refresh_ai_quote_adjustment_cost,
)
from app.slack.evaluation_ai_quote_submit_service import persist_ai_quote_adjustment
from app.slack.media_quote_adjustment import (
    media_selected_target_language_names,
    media_selected_target_languages,
    media_translation_language_costs,
    media_translation_quote_from_session,
    media_translation_quote_uses_adjust_layout,
)
from app.slack.media_quotes import (
    PIPELINE_TRANSCRIBE_TRANSLATE,
    STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
    STAGE_AWAITING_TRANSLATION_ACCEPT,
    STAGE_TRANSLATING,
    media_translation_tokens,
)


def _session(**overrides):
    session = {
        "quote_id": "quote-1",
        "user_id": "U1",
        "team_id": "T1",
        "enterprise_id": None,
        "channel_id": "C1",
        "quote_message_ts": "111.222",
        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
        "stage": STAGE_AWAITING_TRANSLATION_ACCEPT,
        "file_id": "Fmedia",
        "file_name": "product-demo.mp4",
        "source_text_length": 150000,
        "target_languages": ["es", "fr"],
        "target_language_names": ["Spanish", "French"],
        "task_uuid": "task-1",
        "total_tokens": media_translation_tokens(150000, 2),
    }
    session["quote"] = media_translation_quote_from_session(session)
    session.update(overrides)
    return session


class TestMediaTranslationQuoteHelpers:
    def test_quote_from_session_builds_document_mt_shape(self):
        quote = media_translation_quote_from_session(_session())

        assert quote["files"][0]["file_id"] == "Fmedia"
        assert quote["files"][0]["file_name"] == "product-demo.mp4"
        assert quote["files"][0]["character_count"] == 150000
        assert [
            row["target_language"] for row in quote["files"][0]["target_languages"]
        ] == [
            "es",
            "fr",
        ]
        assert quote["total_tokens"] == media_translation_tokens(150000, 2)
        assert quote["pdf_conversion_tokens"] == 0

    def test_quote_from_session_reuses_stored_quote(self):
        stored = {
            "files": [
                {
                    "file_id": "stored",
                    "file_name": "clip.mp4",
                    "character_count": 10,
                    "target_languages": [{"target_language": "de", "tokens": 1}],
                }
            ],
            "total_tokens": 1,
            "pdf_conversion_tokens": 0,
        }

        assert media_translation_quote_from_session(_session(quote=stored)) is stored

    def test_language_costs_use_session_display_names(self):
        rows = media_translation_language_costs(_session())

        assert [(row["file_uuid"], row["value"], row["label"]) for row in rows] == [
            ("Fmedia", "es", "Spanish"),
            ("Fmedia", "fr", "French"),
        ]
        assert rows[0]["file_label"] == "product-demo.mp4"

    def test_selected_languages_default_to_all(self):
        session = _session()
        session.pop("selected_pairs", None)

        assert media_selected_target_languages(session) == ["es", "fr"]

    def test_selected_languages_follow_pairs(self):
        session = _session(selected_pairs=["Fmedia:fr"])

        assert media_selected_target_languages(session) == ["fr"]
        assert media_selected_target_language_names(session, ["fr"]) == ["French"]

    def test_adjust_layout_is_quote2_only(self):
        quote2 = _session()
        quote1 = _session(stage=STAGE_AWAITING_TRANSCRIPTION_ACCEPT)
        quote1.pop("quote")

        assert media_translation_quote_uses_adjust_layout(quote2) is True
        assert media_translation_quote_uses_adjust_layout(quote1) is False
        assert (
            media_translation_quote_uses_adjust_layout(
                _session(stage=STAGE_TRANSLATING)
            )
            is True
        )


@pytest.mark.asyncio
class TestPopulateMediaTranslationQuoteAdjustmentModal:
    async def test_rejects_other_users(self):
        client = AsyncMock()
        with (
            patch(
                "app.slack.evaluation_ai_quote_modal_service.get_media_quote_session",
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
                quote_kind="media_translation",
                user_id="U1",
                context={},
            )

        view = mock_update.await_args.args[2]
        assert "checkboxes" not in str(view)

    async def test_populates_checkbox_modal_from_quote_session(self):
        client = AsyncMock()
        with (
            patch(
                "app.slack.evaluation_ai_quote_modal_service.get_media_quote_session",
                new_callable=AsyncMock,
                return_value=_session(),
            ),
            patch(
                "app.slack.evaluation_ai_quote_modal_service.update_media_quote_session",
                new_callable=AsyncMock,
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
                quote_kind="media_translation",
                user_id="U1",
                context={"channel_id": "C1"},
                channel_id="C1",
                message_ts="111.222",
            )

        modal = mock_update.await_args.args[2]
        assert modal["callback_id"] == "evaluation_ai_quote_adjust_submit"
        metadata = json.loads(modal["private_metadata"])
        assert metadata["quote_kind"] == "media_translation"
        assert metadata["message_ts"] == "111.222"
        rendered = str(modal["blocks"])
        assert "product-demo.mp4" in rendered
        assert "Spanish" in rendered
        assert "French" in rendered
        assert rendered.count("initial_options") == 2


@pytest.mark.asyncio
class TestRefreshMediaTranslationQuoteAdjustmentCost:
    async def test_refresh_recomputes_totals_from_selection(self):
        view = {
            "id": "view-1",
            "state": {
                "values": {
                    "ai_quote_language_Fmedia_es": {
                        "evaluation_ai_quote_language_selection": {
                            "selected_options": [{"value": "Fmedia:es"}],
                        }
                    },
                }
            },
            "blocks": [
                {
                    "block_id": "total_cost_block",
                    "text": {"type": "mrkdwn", "text": "*Total cost:* USD 6.00"},
                }
            ],
        }
        client = AsyncMock()
        with patch(
            "app.slack.evaluation_ai_quote_modal_service.get_media_quote_session",
            new_callable=AsyncMock,
            return_value=_session(),
        ):
            await refresh_ai_quote_adjustment_cost(
                client,
                view=view,
                quote_id="quote-1",
                quote_kind="media_translation",
            )

        updated = client.views_update.await_args.kwargs["view"]
        # One lang: ceil(150000×0.002)=300 tokens → USD 6.00
        assert "*Total cost:* USD 6.00" in str(updated["blocks"])


@pytest.mark.asyncio
class TestPersistMediaTranslationQuoteAdjustment:
    async def test_rejects_wrong_stage(self):
        with patch(
            "app.slack.evaluation_ai_quote_submit_service.get_media_quote_session",
            new_callable=AsyncMock,
            return_value=_session(stage=STAGE_TRANSLATING),
        ):
            persisted = await persist_ai_quote_adjustment(
                AsyncMock(),
                quote_id="quote-1",
                quote_kind="media_translation",
                selected_pairs=["Fmedia:es"],
                user_id="U1",
                context={},
            )

        assert persisted is False

    async def test_persists_pairs_and_refreshes_quote_message(self):
        client = AsyncMock()
        updated_session = _session(selected_pairs=["Fmedia:es"])
        with (
            patch(
                "app.slack.evaluation_ai_quote_submit_service.get_media_quote_session",
                new_callable=AsyncMock,
                return_value=_session(),
            ),
            patch(
                "app.slack.evaluation_ai_quote_submit_service.update_media_quote_session",
                new_callable=AsyncMock,
                return_value=updated_session,
            ) as mock_update_session,
            patch(
                "app.slack.evaluation_ai_quote_submit_service.update_media_translation_quote_slack_message",
                new_callable=AsyncMock,
            ) as mock_update_message,
        ):
            persisted = await persist_ai_quote_adjustment(
                client,
                quote_id="quote-1",
                quote_kind="media_translation",
                selected_pairs=["Fmedia:es"],
                user_id="U1",
                context={},
                channel_id="C1",
                message_ts="111.222",
            )

        assert persisted is True
        updates = mock_update_session.await_args.args[1]
        assert updates["selected_pairs"] == ["Fmedia:es"]
        assert updates["total_tokens"] == media_translation_tokens(150000, 1)
        mock_update_message.assert_awaited_once_with(
            client,
            channel_id="C1",
            message_ts="111.222",
            session=updated_session,
            actions=True,
            status_message=None,
        )

    async def test_empty_selection_cancels_and_fails_submissions(self):
        client = AsyncMock()
        updated_session = _session(selected_pairs=[], stage="cancelled")
        with (
            patch(
                "app.slack.evaluation_ai_quote_submit_service.get_media_quote_session",
                new_callable=AsyncMock,
                return_value=_session(),
            ),
            patch(
                "app.slack.evaluation_ai_quote_submit_service.update_media_quote_session",
                new_callable=AsyncMock,
                return_value=updated_session,
            ) as mock_update_session,
            patch(
                "app.slack.evaluation_ai_quote_submit_service.update_media_translation_quote_slack_message",
                new_callable=AsyncMock,
            ) as mock_update_message,
            patch(
                "app.ray.events.media_pipeline_events.fail_media_submissions",
                new_callable=AsyncMock,
            ) as mock_fail,
        ):
            persisted = await persist_ai_quote_adjustment(
                client,
                quote_id="quote-1",
                quote_kind="media_translation",
                selected_pairs=[],
                user_id="U1",
                context={},
                channel_id="C1",
                message_ts="111.222",
            )

        assert persisted is True
        assert mock_update_session.await_args.args[1]["stage"] == "cancelled"
        mock_fail.assert_awaited_once_with(updated_session)
        mock_update_message.assert_awaited_once()
        assert mock_update_message.await_args.kwargs["actions"] is False
        assert (
            mock_update_message.await_args.kwargs["status_message"]
            == "AI Translate quote cancelled."
        )
