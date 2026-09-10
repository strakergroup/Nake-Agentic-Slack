"""Tests for Configure media modal view parsing."""

from __future__ import annotations

import json

import pytest

from app.media.media_workflow import MediaWorkflowType


def _option(value: str, text: str = "") -> dict:
    return {"value": value, "text": {"type": "plain_text", "text": text or value}}


def _view(*, metadata: dict, values: dict) -> dict:
    return {
        "private_metadata": json.dumps(metadata),
        "state": {"values": values},
    }


def _files() -> list[dict]:
    return [{"file_id": "F1", "file_name": "clip.mp4", "duration_ms": 1000}]


def test_parse_translate_selection_with_embed_and_review_flags():
    from app.slack.media_configure import parse_video_configure_media_view

    selection = parse_video_configure_media_view(
        _view(
            metadata={
                "channel_id": "C1",
                "files": _files(),
                "thread_ts": "1.2",
                "show_embed_option": True,
            },
            values={
                "workflow_type": {
                    "video_configure_workflow_type": {
                        "selected_option": _option("transcribe_translate"),
                    }
                },
                "selected_file": {
                    "file_display": {"selected_options": [_option("F1")]}
                },
                "target_languages": {
                    "language_mt_options": {
                        "selected_options": [
                            _option("fr", "French"),
                            _option("de", "German"),
                        ]
                    }
                },
                "embed_source": {
                    "embed_source_options": {
                        "selected_options": [_option("embed_source")]
                    }
                },
                "embed_translated": {
                    "embed_translated_options": {
                        "selected_options": [_option("embed_translated")]
                    }
                },
                "review_gate": {
                    "review_gate_options": {
                        "selected_options": [_option("review_gate")]
                    }
                },
            },
        )
    )
    assert selection.workflow_type is MediaWorkflowType.TRANSCRIBE_TRANSLATE
    assert selection.target_languages == ["fr", "de"]
    assert selection.target_language_names == ["French", "German"]
    assert selection.embed_source is True
    assert selection.embed_translated is True
    assert selection.review_gate is True
    assert selection.files == _files()
    assert selection.channel_id == "C1"
    assert selection.thread_ts == "1.2"


def test_parse_transcribe_only_clears_languages_and_translated_embed():
    from app.slack.media_configure import parse_video_configure_media_view

    selection = parse_video_configure_media_view(
        _view(
            metadata={
                "channel_id": "C1",
                "files": _files(),
                "show_embed_option": True,
            },
            values={
                "workflow_type": {
                    "video_configure_workflow_type": {
                        "selected_option": _option("transcribe_only"),
                    }
                },
                "selected_file": {
                    "file_display": {"selected_options": [_option("F1")]}
                },
                "embed_translated": {
                    "embed_translated_options": {
                        "selected_options": [_option("embed_translated")]
                    }
                },
                "review_gate": {"review_gate_options": {"selected_options": []}},
            },
        )
    )
    assert selection.workflow_type is MediaWorkflowType.TRANSCRIBE_ONLY
    assert selection.target_languages == []
    assert selection.embed_translated is False
    assert selection.review_gate is False


def test_parse_omitted_review_gate_block_is_off():
    """Slack omits optional unchecked checkboxes from view.state.values."""
    from app.slack.media_configure import parse_video_configure_media_view

    selection = parse_video_configure_media_view(
        _view(
            metadata={
                "channel_id": "C1",
                "files": _files(),
                "show_embed_option": True,
            },
            values={
                "workflow_type": {
                    "video_configure_workflow_type": {
                        "selected_option": _option("transcribe_only"),
                    }
                },
                "selected_file": {
                    "file_display": {"selected_options": [_option("F1")]}
                },
            },
        )
    )
    assert selection.review_gate is False


def test_parse_rejects_malformed_private_metadata():
    from app.slack.media_configure import (
        VideoConfigureMediaError,
        parse_video_configure_media_view,
    )

    with pytest.raises(VideoConfigureMediaError):
        parse_video_configure_media_view(
            {"private_metadata": "{not-json", "state": {"values": {}}}
        )


def test_parse_requires_target_languages_for_translate():
    from app.slack.media_configure import (
        VideoConfigureMediaError,
        parse_video_configure_media_view,
    )

    with pytest.raises(VideoConfigureMediaError):
        parse_video_configure_media_view(
            _view(
                metadata={
                    "channel_id": "C1",
                    "files": _files(),
                    "show_embed_option": True,
                },
                values={
                    "workflow_type": {
                        "video_configure_workflow_type": {
                            "selected_option": _option("transcribe_translate"),
                        }
                    },
                    "selected_file": {
                        "file_display": {"selected_options": [_option("F1")]}
                    },
                    "target_languages": {
                        "language_mt_options": {"selected_options": []}
                    },
                },
            )
        )


def test_parse_requires_selected_files():
    from app.slack.media_configure import (
        VideoConfigureMediaError,
        parse_video_configure_media_view,
    )

    with pytest.raises(VideoConfigureMediaError):
        parse_video_configure_media_view(
            _view(
                metadata={
                    "channel_id": "C1",
                    "files": _files(),
                    "show_embed_option": True,
                },
                values={
                    "workflow_type": {
                        "video_configure_workflow_type": {
                            "selected_option": _option("transcribe_only"),
                        }
                    },
                    "selected_file": {"file_display": {"selected_options": []}},
                },
            )
        )


def test_parse_audio_only_forces_embed_flags_off():
    from app.slack.media_configure import parse_video_configure_media_view

    selection = parse_video_configure_media_view(
        _view(
            metadata={
                "channel_id": "C1",
                "files": _files(),
                "show_embed_option": False,
            },
            values={
                "workflow_type": {
                    "video_configure_workflow_type": {
                        "selected_option": _option("transcribe_translate"),
                    }
                },
                "selected_file": {
                    "file_display": {"selected_options": [_option("F1")]}
                },
                "target_languages": {
                    "language_mt_options": {
                        "selected_options": [_option("fr", "French")]
                    }
                },
                "embed_source": {
                    "embed_source_options": {
                        "selected_options": [_option("embed_source")]
                    }
                },
            },
        )
    )
    assert selection.embed_source is False
    assert selection.embed_translated is False


def test_configure_media_quote_fields_use_translate_pipeline_not_legacy_embed():
    from app.slack.media_configure import (
        VideoConfigureMediaSelection,
        configure_media_quote_fields,
    )
    from app.slack.media_quotes import (
        PIPELINE_TRANSCRIBE,
        PIPELINE_TRANSCRIBE_TRANSLATE,
    )

    translate = configure_media_quote_fields(
        VideoConfigureMediaSelection(
            workflow_type=MediaWorkflowType.TRANSCRIBE_TRANSLATE,
            embed_source=True,
            embed_translated=True,
            review_gate=True,
            target_languages=["fr"],
            target_language_names=["French"],
            files=_files(),
            channel_id="C1",
        )
    )
    assert translate["pipeline_kind"] == PIPELINE_TRANSCRIBE_TRANSLATE
    assert translate["extra"] == {
        "embed_source": True,
        "embed_translated": True,
        "review_gate": True,
        "workflow_type": "transcribe_translate",
    }

    transcribe = configure_media_quote_fields(
        VideoConfigureMediaSelection(
            workflow_type=MediaWorkflowType.TRANSCRIBE_ONLY,
            embed_source=True,
            embed_translated=False,
            review_gate=False,
            target_languages=[],
            target_language_names=[],
            files=_files(),
            channel_id="C1",
        )
    )
    assert transcribe["pipeline_kind"] == PIPELINE_TRANSCRIBE
    assert transcribe["extra"]["embed_source"] is True
    assert transcribe["extra"]["review_gate"] is False


class _FakeRayContext(dict):
    enterprise_id = None


@pytest.mark.asyncio
async def test_workflow_type_change_rebuilds_modal_without_languages():
    from unittest.mock import AsyncMock

    from app.slack.handlers.media import handle_video_configure_workflow_type

    client = AsyncMock()
    body = {
        "view": {
            "id": "V1",
            "private_metadata": json.dumps(
                {
                    "channel_id": "C1",
                    "files": _files(),
                    "thread_ts": "1.2",
                    "show_embed_option": True,
                }
            ),
        }
    }
    action = {"selected_option": {"value": "transcribe_only"}}
    await handle_video_configure_workflow_type(client=client, body=body, action=action)
    client.views_update.assert_awaited_once()
    view = client.views_update.await_args.kwargs["view"]
    block_ids = [block.get("block_id") for block in view["blocks"]]
    assert "target_languages" not in block_ids
    assert "embed_translated" not in block_ids


@pytest.mark.asyncio
async def test_workflow_type_change_keeps_unchecked_review_gate_and_embed():
    from unittest.mock import AsyncMock

    from app.slack.handlers.media import handle_video_configure_workflow_type

    client = AsyncMock()
    body = {
        "view": {
            "id": "V1",
            "private_metadata": json.dumps(
                {
                    "channel_id": "C1",
                    "files": _files(),
                    "thread_ts": "1.2",
                    "show_embed_option": True,
                }
            ),
            "state": {
                "values": {
                    "embed_source": {
                        "embed_source_options": {
                            "selected_options": [_option("embed_source")]
                        }
                    },
                    "review_gate": {"review_gate_options": {"selected_options": []}},
                }
            },
        }
    }
    action = {"selected_option": {"value": "transcribe_only"}}
    await handle_video_configure_workflow_type(client=client, body=body, action=action)
    view = client.views_update.await_args.kwargs["view"]
    review = next(b for b in view["blocks"] if b.get("block_id") == "review_gate")
    embed = next(b for b in view["blocks"] if b.get("block_id") == "embed_source")
    assert not review["element"].get("initial_options")
    assert [opt["value"] for opt in embed["element"]["initial_options"]] == [
        "embed_source"
    ]


@pytest.mark.asyncio
async def test_configure_submit_creates_quote1_with_embed_source_flag():
    from unittest.mock import AsyncMock, patch

    from app.slack.handlers.media_submissions import handle_video_configure_media_submit

    view = _view(
        metadata={
            "channel_id": "C1",
            "files": _files(),
            "thread_ts": "1.2",
            "show_embed_option": True,
        },
        values={
            "workflow_type": {
                "video_configure_workflow_type": {
                    "selected_option": _option("transcribe_only"),
                }
            },
            "selected_file": {"file_display": {"selected_options": [_option("F1")]}},
            "embed_source": {
                "embed_source_options": {"selected_options": [_option("embed_source")]}
            },
            "review_gate": {
                "review_gate_options": {"selected_options": [_option("review_gate")]}
            },
        },
    )
    context = _FakeRayContext(
        {
            "user_id": "U1",
            "team_id": "T1",
            "channel_id": "C1",
            "ray": object(),
        }
    )
    client = AsyncMock()
    client.token = "xoxb-test"
    client.files_info.return_value = {
        "file": {
            "url_private_download": "https://example.com/clip.mp4",
            "duration_ms": 60000,
        }
    }
    submission = type("Submission", (), {"id": 99})()

    with (
        patch(
            "app.slack.handlers.media_submissions.require_ray_client",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.slack.handlers.media_submissions.check_and_record_transcription_only_submission_async",
            new_callable=AsyncMock,
            return_value=(False, submission),
        ),
        patch(
            "app.slack.handlers.media_submissions.file_info_with_quote_duration",
            new_callable=AsyncMock,
            side_effect=lambda file_info, *_args, **_kwargs: file_info,
        ),
        patch(
            "app.slack.handlers.media_submissions.create_media_quote_session",
            new_callable=AsyncMock,
        ) as create_session,
        patch(
            "app.slack.handlers.media_submissions.post_or_auto_start_media_quote",
            new_callable=AsyncMock,
        ),
    ):
        create_session.return_value = {"quote_id": "q1"}
        await handle_video_configure_media_submit(
            view=view, context=context, client=client
        )

    create_session.assert_awaited_once()
    kwargs = create_session.await_args.kwargs
    assert kwargs["pipeline_kind"] == "transcribe"
    assert kwargs["extra"]["embed_source"] is True
    assert kwargs["extra"]["review_gate"] is True
    assert kwargs["extra"]["workflow_type"] == "transcribe_only"
