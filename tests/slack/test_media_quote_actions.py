"""Tests for media quote accept / cancel action helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.slack.media_quotes import (
    PIPELINE_TRANSCRIBE,
    PIPELINE_TRANSCRIBE_TRANSLATE,
    STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
    STAGE_AWAITING_TRANSLATION_ACCEPT,
    STAGE_TRANSCRIBING,
)


@pytest.mark.asyncio
async def test_accept_media_quote_requires_awaiting_stage():
    from app.slack.media_quote_actions import accept_media_quote

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {
        "user_id": "U1",
        "team_id": "T1",
    }.get(key)
    context.enterprise_id = None
    context.get = lambda key, default=None: None

    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": STAGE_TRANSCRIBING,
        "pipeline_kind": PIPELINE_TRANSCRIBE,
        "total_tokens": 100,
        "channel_id": "C1",
    }

    with patch(
        "app.slack.media_quote_actions.get_media_quote_session",
        new=AsyncMock(return_value=session),
    ):
        with patch("app.slack.media_quote_actions.redis_conn") as mock_redis:
            mock_redis.set = AsyncMock(return_value=True)
            mock_redis.delete = AsyncMock()
            await accept_media_quote(
                client=client,
                body={"channel": {"id": "C1"}, "message": {"ts": "1.2"}},
                action={"value": "q1"},
                context=context,
            )

    client.chat_postMessage.assert_awaited()
    assert "not ready" in client.chat_postMessage.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_accept_media_quote_lock_contention():
    from app.slack.media_quote_actions import accept_media_quote

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {
        "user_id": "U1",
        "team_id": "T1",
    }.get(key)
    context.enterprise_id = None
    context.get = lambda key, default=None: None

    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
        "pipeline_kind": PIPELINE_TRANSCRIBE,
        "total_tokens": 100,
        "channel_id": "C1",
    }

    with patch(
        "app.slack.media_quote_actions.get_media_quote_session",
        new=AsyncMock(return_value=session),
    ):
        with patch("app.slack.media_quote_actions.redis_conn") as mock_redis:
            mock_redis.set = AsyncMock(return_value=False)
            await accept_media_quote(
                client=client,
                body={},
                action={"value": "q1"},
                context=context,
            )

    assert "already in progress" in client.chat_postMessage.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_accept_translation_quote_resumes_phase():
    from app.slack.media_quote_actions import accept_media_translation_quote

    client = AsyncMock()
    context = {
        "user_id": "U1",
        "team_id": "T1",
        "ray": MagicMock(),
    }
    context_obj = MagicMock()
    context_obj.__getitem__ = lambda self, key: context[key]
    context_obj.get = lambda key, default=None: context.get(key, default)
    context_obj.enterprise_id = None
    context_obj["ray"].client = MagicMock()
    context_obj["ray"].client.id_token = "tok"
    context_obj["ray"].super_group = None

    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": STAGE_AWAITING_TRANSLATION_ACCEPT,
        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
        "total_tokens": 2,
        "channel_id": "C1",
        "task_uuid": "task-1",
        "target_languages": ["es"],
        "thread_ts": None,
    }

    with patch(
        "app.slack.media_quote_actions.get_media_quote_session",
        new=AsyncMock(return_value=session),
    ):
        with patch("app.slack.media_quote_actions.redis_conn") as mock_redis:
            mock_redis.set = AsyncMock(return_value=True)
            mock_redis.delete = AsyncMock()
            with patch(
                "app.slack.media_quote_actions._require_ai_token_balance",
                new=AsyncMock(return_value=True),
            ):
                with patch(
                    "app.slack.media_quote_actions._resume_translate_phase",
                    new=AsyncMock(),
                ) as mock_resume:
                    with patch(
                        "app.slack.media_quote_actions.update_media_quote_session",
                        new=AsyncMock(return_value={**session, "stage": "translating"}),
                    ):
                        with patch(
                            "app.slack.media_quote_actions._update_quote_message",
                            new=AsyncMock(),
                        ):
                            await accept_media_translation_quote(
                                client=client,
                                body={
                                    "channel": {"id": "C1"},
                                    "message": {"ts": "1.2"},
                                },
                                action={"value": "q1"},
                                context=context_obj,
                            )

    mock_resume.assert_awaited_once()
    assert mock_resume.await_args.kwargs["task_uuid"] == "task-1"


@pytest.mark.asyncio
async def test_accept_translation_quote_reprices_selected_pairs():
    from app.slack.media_quote_actions import accept_media_translation_quote
    from app.slack.media_quotes import media_translation_tokens

    client = AsyncMock()
    context = {
        "user_id": "U1",
        "team_id": "T1",
        "ray": MagicMock(),
    }
    context_obj = MagicMock()
    context_obj.__getitem__ = lambda self, key: context[key]
    context_obj.get = lambda key, default=None: context.get(key, default)
    context_obj.enterprise_id = None
    context_obj["ray"].client = MagicMock()
    context_obj["ray"].client.id_token = "tok"
    context_obj["ray"].super_group = None

    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": STAGE_AWAITING_TRANSLATION_ACCEPT,
        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
        "total_tokens": media_translation_tokens(1000, 2),
        "channel_id": "C1",
        "task_uuid": "task-1",
        "file_id": "F1",
        "file_name": "clip.mp4",
        "source_text_length": 1000,
        "target_languages": ["es", "fr"],
        "target_language_names": ["Spanish", "French"],
        "selected_pairs": ["F1:es"],
        "thread_ts": None,
    }

    with patch(
        "app.slack.media_quote_actions.get_media_quote_session",
        new=AsyncMock(return_value=session),
    ):
        with patch("app.slack.media_quote_actions.redis_conn") as mock_redis:
            mock_redis.set = AsyncMock(return_value=True)
            mock_redis.delete = AsyncMock()
            with patch(
                "app.slack.media_quote_actions._require_ai_token_balance",
                new=AsyncMock(return_value=True),
            ) as mock_balance:
                with patch(
                    "app.slack.media_quote_actions._resume_translate_phase",
                    new=AsyncMock(),
                ) as mock_resume:
                    with patch(
                        "app.slack.media_quote_actions.update_media_quote_session",
                        new=AsyncMock(return_value={**session, "stage": "translating"}),
                    ):
                        with patch(
                            "app.slack.media_quote_actions._update_quote_message",
                            new=AsyncMock(),
                        ):
                            await accept_media_translation_quote(
                                client=client,
                                body={
                                    "channel": {"id": "C1"},
                                    "message": {"ts": "1.2"},
                                },
                                action={"value": "q1"},
                                context=context_obj,
                            )

    assert mock_balance.await_args.args[2] == media_translation_tokens(1000, 1)
    assert mock_resume.await_args.kwargs["session"]["selected_pairs"] == ["F1:es"]


@pytest.mark.asyncio
async def test_accept_translation_quote_includes_translated_embed_in_balance():
    from app.slack.media_quote_actions import accept_media_translation_quote
    from app.slack.media_quotes import (
        embedding_tokens_for_duration,
        media_translation_tokens,
    )

    client = AsyncMock()
    context = {
        "user_id": "U1",
        "team_id": "T1",
        "ray": MagicMock(),
    }
    context_obj = MagicMock()
    context_obj.__getitem__ = lambda self, key: context[key]
    context_obj.get = lambda key, default=None: context.get(key, default)
    context_obj.enterprise_id = None
    context_obj["ray"].client = MagicMock()
    context_obj["ray"].client.id_token = "tok"
    context_obj["ray"].super_group = None

    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": STAGE_AWAITING_TRANSLATION_ACCEPT,
        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
        "total_tokens": media_translation_tokens(1000, 2),
        "channel_id": "C1",
        "task_uuid": "task-1",
        "file_id": "F1",
        "file_name": "clip.mp4",
        "source_text_length": 1000,
        "target_languages": ["es", "fr"],
        "target_language_names": ["Spanish", "French"],
        "selected_pairs": ["F1:es"],
        "thread_ts": None,
        "embed_translated": True,
        "duration_ms": 60_000,
    }

    with patch(
        "app.slack.media_quote_actions.get_media_quote_session",
        new=AsyncMock(return_value=session),
    ):
        with patch("app.slack.media_quote_actions.redis_conn") as mock_redis:
            mock_redis.set = AsyncMock(return_value=True)
            mock_redis.delete = AsyncMock()
            with patch(
                "app.slack.media_quote_actions._require_ai_token_balance",
                new=AsyncMock(return_value=True),
            ) as mock_balance:
                with patch(
                    "app.slack.media_quote_actions._resume_translate_phase",
                    new=AsyncMock(),
                ):
                    with patch(
                        "app.slack.media_quote_actions.update_media_quote_session",
                        new=AsyncMock(return_value={**session, "stage": "translating"}),
                    ):
                        with patch(
                            "app.slack.media_quote_actions._update_quote_message",
                            new=AsyncMock(),
                        ):
                            await accept_media_translation_quote(
                                client=client,
                                body={
                                    "channel": {"id": "C1"},
                                    "message": {"ts": "1.2"},
                                },
                                action={"value": "q1"},
                                context=context_obj,
                            )

    assert mock_balance.await_args.args[2] == media_translation_tokens(
        1000, 1
    ) + embedding_tokens_for_duration(60_000, 1)


@pytest.mark.asyncio
async def test_accept_configure_translation_quote_tells_user_when_reducer_rejects():
    from app.media.media_workflow import (
        MediaWorkflowEvent,
        MediaWorkflowStage,
        MediaWorkflowTransitionError,
    )
    from app.slack.media_quote_actions import accept_media_translation_quote

    client = AsyncMock()
    context_obj = MagicMock()
    context_obj.__getitem__ = lambda self, key: {"user_id": "U1", "team_id": "T1"}[key]
    context_obj.get = lambda key, default=None: None
    context_obj.enterprise_id = None

    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": STAGE_AWAITING_TRANSLATION_ACCEPT,
        "workflow_type": "transcribe_translate",
        "embed_source": False,
        "embed_translated": False,
        "review_gate": True,
        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
        "total_tokens": 2,
        "channel_id": "C1",
        "task_uuid": "task-1",
        "target_languages": ["es"],
        "thread_ts": None,
    }

    with (
        patch(
            "app.slack.media_quote_actions.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch("app.slack.media_quote_actions.redis_conn") as mock_redis,
        patch(
            "app.slack.media_quote_actions._require_ai_token_balance",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.slack.media_quote_actions.advance_media_workflow",
            create=True,
            side_effect=MediaWorkflowTransitionError(
                MediaWorkflowStage.AWAITING_TRANSLATION_ACCEPT,
                MediaWorkflowEvent.QUOTE2_ACCEPTED,
            ),
        ),
        patch(
            "app.slack.media_quote_actions._resume_translate_phase",
            new=AsyncMock(),
        ) as mock_resume,
        patch("app.slack.media_quote_actions.notify_exception"),
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        await accept_media_translation_quote(
            client=client,
            body={"channel": {"id": "C1"}, "message": {"ts": "1.2"}},
            action={"value": "q1"},
            context=context_obj,
        )

    mock_resume.assert_not_awaited()
    assert "not ready" in client.chat_postMessage.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_cancel_media_quote_fails_submissions():
    from app.slack.media_quote_actions import cancel_media_quote

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {"user_id": "U1"}.get(key)
    context.enterprise_id = None

    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
        "pipeline_kind": PIPELINE_TRANSCRIBE,
        "submission_id": 55,
        "channel_id": "C1",
    }

    with (
        patch(
            "app.slack.media_quote_actions.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.slack.media_quote_actions.update_media_quote_session",
            new=AsyncMock(),
        ),
        patch(
            "app.slack.media_quote_actions.delete_media_quote_session",
            new=AsyncMock(),
        ),
        patch(
            "app.ray.events.media_pipeline_events.fail_media_submissions",
            new=AsyncMock(),
        ) as mock_fail,
    ):
        await cancel_media_quote(
            client=client,
            body={"channel": {"id": "C1"}, "message": {"ts": "1.2"}},
            action={"value": "q1"},
            context=context,
        )

    mock_fail.assert_awaited_once_with(session)
    client.chat_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_or_auto_start_falls_back_to_quote_when_balance_fails():
    """Non-admin auto-start must not strand the submission without Accept UI."""
    from app.slack.media_quote_actions import post_or_auto_start_media_quote

    client = AsyncMock()
    context = MagicMock()
    context.get = lambda key, default=None: None
    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "channel_id": "C1",
        "stage": STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
        "pipeline_kind": PIPELINE_TRANSCRIBE,
        "total_tokens": 100,
    }

    with (
        patch(
            "app.slack.media_quote_actions.user_may_receive_quotes",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.slack.media_quote_actions.update_media_quote_session",
            new=AsyncMock(return_value={**session, "auto_proceed": True}),
        ),
        patch(
            "app.slack.media_quote_actions.get_media_quote_session",
            new=AsyncMock(return_value={**session, "auto_proceed": True}),
        ),
        patch(
            "app.slack.media_quote_actions.accept_media_quote",
            new=AsyncMock(return_value=False),
        ) as mock_accept,
        patch(
            "app.slack.media_quote_actions.post_media_quote_message",
            new=AsyncMock(),
        ) as mock_post,
    ):
        await post_or_auto_start_media_quote(client, context, session)

    mock_accept.assert_awaited_once()
    mock_post.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_accept_translation_returns_false_without_ray_client():
    from app.slack.media_quote_actions import (
        auto_accept_media_translation_quote_if_needed,
    )

    client = AsyncMock()
    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "team_id": "T1",
        "auto_proceed": True,
        "channel_id": "C1",
    }

    with (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[]),
        ),
    ):
        handled = await auto_accept_media_translation_quote_if_needed(client, session)

    assert handled is False


@pytest.mark.asyncio
async def test_resume_configure_source_embed_does_not_reuse_translation_task():
    from app.slack.media_configure_embed import resume_configure_embed_phase

    source_task = SimpleNamespace(
        task_uuid="asr-task",
        extra_data={"media_quote_id": "q1"},
        result_file_id="srt-source",
        translated_file_ids=None,
        client_id="client-1",
        file_name="clip.mp4",
        download_url="https://files.example/clip.mp4",
        bot_token="xoxb-test",
        model="whisper-1",
        service="azure",
        app_source="slack",
    )
    executed = []

    class _FakeDb:
        async def get(self, model, key):
            return source_task

        async def execute(self, stmt):
            executed.append(stmt)

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    session = {
        "quote_id": "q1",
        "task_uuid": "asr-task",
        "workflow_type": "transcribe_translate",
        "file_id": "F1",
        "download_url": "https://files.example/clip.mp4",
        "file_name": "clip.mp4",
        "approved_source_srt_file_id": "srt-approved",
        "target_languages": ["es"],
    }

    with (
        patch(
            "app.slack.media_configure_embed.AsyncSession",
            return_value=_FakeDb(),
        ),
        patch(
            "app.slack.media_configure_embed.create_asr_task",
            new_callable=AsyncMock,
            return_value="embed-task",
        ) as mock_create,
        patch("app.slack.media_configure_embed.httpx.AsyncClient") as mock_http,
    ):
        await resume_configure_embed_phase(session=session, translated=False)

    mock_create.assert_awaited_once()
    asr_task = mock_create.await_args.args[0]
    assert asr_task.extra_data["pipeline_type"] == "embed"
    assert asr_task.extra_data["embed_role"] == "source"
    assert asr_task.extra_data["srt_file_ids"] == ["srt-approved"]
    assert asr_task.extra_data["media_quote_id"] == "q1"
    assert executed == []
    mock_http.assert_not_called()


@pytest.mark.asyncio
async def test_configure_embed_keeps_existing_transcription_extra_data():
    from app.slack.media_configure_embed import resume_configure_embed_phase

    stored_extra = {
        "media_quote_id": "old",
        "slack_channel_id": "C1",
        "requester_email": "poster@example.com",
    }
    source_task = SimpleNamespace(
        task_uuid="asr-task",
        extra_data=stored_extra,
        result_file_id="srt-source",
        translated_file_ids=None,
        client_id="client-1",
        file_name="clip.mp4",
        download_url="https://files.example/clip.mp4",
        bot_token="xoxb-test",
        model="whisper-1",
        service="azure",
        app_source="slack",
    )

    class _FakeDb:
        async def get(self, model, key):
            return source_task

        async def execute(self, stmt):
            return None

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    session = {
        "quote_id": "q1",
        "task_uuid": "asr-task",
        "workflow_type": "transcribe_only",
        "file_id": "F1",
        "download_url": "https://files.example/clip.mp4",
        "file_name": "clip.mp4",
        "approved_source_srt_file_id": "srt-approved",
        "target_languages": [],
    }

    with (
        patch(
            "app.slack.media_configure_embed.AsyncSession",
            return_value=_FakeDb(),
        ),
        patch(
            "app.slack.media_configure_embed.create_asr_task",
            new_callable=AsyncMock,
            return_value="embed-task",
        ) as mock_create,
        patch("app.slack.media_configure_embed.httpx.AsyncClient"),
    ):
        await resume_configure_embed_phase(session=session, translated=False)

    asr_task = mock_create.await_args.args[0]
    assert asr_task.extra_data["slack_channel_id"] == "C1"
    assert asr_task.extra_data["requester_email"] == "poster@example.com"
    assert asr_task.extra_data["media_quote_id"] == "q1"
    assert asr_task.extra_data["embed_role"] == "source"
    assert stored_extra["media_quote_id"] == "old"


@pytest.mark.asyncio
async def test_configure_embed_rejects_invalid_transcription_extra_data():
    from app.slack.media_configure_embed import (
        TranscriptionTaskExtraDataError,
        resume_configure_embed_phase,
    )

    source_task = SimpleNamespace(
        task_uuid="asr-task",
        extra_data=["not-a-mapping"],
        result_file_id="srt-source",
        translated_file_ids=None,
        client_id="client-1",
        file_name="clip.mp4",
        download_url="https://files.example/clip.mp4",
        bot_token="xoxb-test",
        model="whisper-1",
        service="azure",
        app_source="slack",
    )

    class _FakeDb:
        async def get(self, model, key):
            return source_task

        async def execute(self, stmt):
            return None

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    session = {
        "quote_id": "q1",
        "task_uuid": "asr-task",
        "workflow_type": "transcribe_only",
        "file_id": "F1",
        "download_url": "https://files.example/clip.mp4",
        "file_name": "clip.mp4",
        "approved_source_srt_file_id": "srt-approved",
        "target_languages": [],
    }

    with patch(
        "app.slack.media_configure_embed.AsyncSession",
        return_value=_FakeDb(),
    ):
        with pytest.raises(TranscriptionTaskExtraDataError):
            await resume_configure_embed_phase(session=session, translated=False)


@pytest.mark.asyncio
async def test_replaced_translated_srt_keeps_other_target_languages():
    from app.slack.media_configure_embed import resume_configure_embed_phase

    source_task = SimpleNamespace(
        task_uuid="asr-task",
        extra_data={"media_quote_id": "q1"},
        result_file_id="srt-source",
        translated_file_ids={"es": "srt-es", "fr": "srt-fr"},
        detected_language="en",
        client_id="client-1",
        file_name="clip.mp4",
        download_url="https://files.example/clip.mp4",
        bot_token="xoxb-test",
        model="whisper-1",
        service="azure",
        app_source="slack",
    )

    class _FakeDb:
        async def get(self, model, key):
            return source_task

        async def execute(self, stmt):
            return None

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    session = {
        "quote_id": "q1",
        "task_uuid": "asr-task",
        "workflow_type": "transcribe_translate",
        "file_id": "F1",
        "download_url": "https://files.example/clip.mp4",
        "file_name": "clip.mp4",
        "approved_translated_srt_file_id": "srt-replaced",
        "approved_translated_srt_language": "es",
        "embed_source": True,
        "embed_translated": True,
        "target_languages": ["es", "fr"],
    }

    with (
        patch(
            "app.slack.media_configure_embed.AsyncSession",
            return_value=_FakeDb(),
        ),
        patch(
            "app.slack.media_configure_embed.create_asr_task",
            new_callable=AsyncMock,
            return_value="embed-task",
        ) as mock_create,
        patch("app.slack.media_configure_embed.httpx.AsyncClient") as mock_http,
    ):
        await resume_configure_embed_phase(session=session, translated=True)

    asr_task = mock_create.await_args.args[0]
    assert asr_task.extra_data["srt_file_ids"] == [
        "srt-source",
        "srt-replaced",
        "srt-fr",
    ]
    assert asr_task.extra_data["language_codes"] == ["en", "es", "fr"]
    assert asr_task.extra_data["target_languages"] == ["es", "fr"]
    mock_http.assert_not_called()


@pytest.mark.asyncio
async def test_translated_embed_muxes_source_and_all_target_tracks():
    from app.slack.media_configure_embed import resume_configure_embed_phase

    source_task = SimpleNamespace(
        task_uuid="asr-task",
        extra_data={"media_quote_id": "q1"},
        result_file_id="srt-source",
        translated_file_ids={"fi": "srt-fi", "es": "srt-es"},
        detected_language="en",
        client_id="client-1",
        file_name="clip.mp4",
        download_url="https://files.example/clip.mp4",
        bot_token="xoxb-test",
        model="whisper-1",
        service="azure",
        app_source="slack",
    )

    class _FakeDb:
        async def get(self, model, key):
            return source_task

        async def execute(self, stmt):
            return None

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    session = {
        "quote_id": "q1",
        "task_uuid": "asr-task",
        "workflow_type": "transcribe_translate",
        "file_id": "F1",
        "download_url": "https://files.example/clip.mp4",
        "file_name": "clip.mp4",
        "embed_source": True,
        "embed_translated": True,
        "target_languages": ["fi", "es"],
    }

    with (
        patch(
            "app.slack.media_configure_embed.AsyncSession",
            return_value=_FakeDb(),
        ),
        patch(
            "app.slack.media_configure_embed.create_asr_task",
            new_callable=AsyncMock,
            return_value="embed-task",
        ) as mock_create,
        patch("app.slack.media_configure_embed.httpx.AsyncClient") as mock_http,
    ):
        await resume_configure_embed_phase(session=session, translated=True)

    asr_task = mock_create.await_args.args[0]
    assert asr_task.extra_data["srt_file_ids"] == ["srt-source", "srt-fi", "srt-es"]
    assert asr_task.extra_data["language_codes"] == ["en", "fi", "es"]
    assert asr_task.extra_data["target_languages"] == ["fi", "es"]
    mock_http.assert_not_called()


def test_translated_only_embed_tracks_omit_source():
    from app.slack.media_configure_embed import _configure_embed_tracks

    task = SimpleNamespace(
        result_file_id="srt-source",
        translated_file_ids={"fi": "srt-fi", "es": "srt-es"},
        detected_language="en",
    )
    ids, langs, billing = _configure_embed_tracks(
        session={
            "embed_source": False,
            "embed_translated": True,
            "target_languages": ["fi", "es"],
        },
        task=task,
        translated=True,
    )
    assert ids == ["srt-fi", "srt-es"]
    assert langs == ["fi", "es"]
    assert billing == ["fi", "es"]


@pytest.mark.asyncio
async def test_translated_embed_ignores_replacement_without_language():
    from app.slack.media_configure_embed import resume_configure_embed_phase

    source_task = SimpleNamespace(
        task_uuid="asr-task",
        extra_data={"media_quote_id": "q1"},
        result_file_id="srt-source",
        translated_file_ids={"es": "srt-es", "fr": "srt-fr"},
        detected_language="en",
        client_id="client-1",
        file_name="clip.mp4",
        download_url="https://files.example/clip.mp4",
        bot_token="xoxb-test",
        model="whisper-1",
        service="azure",
        app_source="slack",
    )

    class _FakeDb:
        async def get(self, model, key):
            return source_task

        async def execute(self, stmt):
            return None

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    session = {
        "quote_id": "q1",
        "task_uuid": "asr-task",
        "workflow_type": "transcribe_translate",
        "file_id": "F1",
        "download_url": "https://files.example/clip.mp4",
        "file_name": "clip.mp4",
        "approved_translated_srt_file_id": "srt-replaced",
        "embed_source": True,
        "embed_translated": True,
        "target_languages": ["es", "fr"],
    }

    with (
        patch(
            "app.slack.media_configure_embed.AsyncSession",
            return_value=_FakeDb(),
        ),
        patch(
            "app.slack.media_configure_embed.create_asr_task",
            new_callable=AsyncMock,
        ) as mock_create,
        patch("app.slack.media_configure_embed.httpx.AsyncClient"),
    ):
        await resume_configure_embed_phase(session=session, translated=True)

    asr_task = mock_create.await_args.args[0]
    assert asr_task.extra_data["srt_file_ids"] == ["srt-source", "srt-es", "srt-fr"]
    assert "srt-replaced" not in asr_task.extra_data["srt_file_ids"]


@pytest.mark.asyncio
async def test_resume_configure_embed_copies_duration_ms_onto_new_task():
    from app.slack.media_configure_embed import resume_configure_embed_phase

    source_task = SimpleNamespace(
        task_uuid="asr-task",
        extra_data={"media_quote_id": "q1"},
        result_file_id="srt-source",
        translated_file_ids=None,
        client_id="client-1",
        file_name="clip.mp4",
        download_url="https://files.example/clip.mp4",
        bot_token="xoxb-test",
        model="whisper-1",
        service="azure",
        app_source="slack",
        duration_ms=90_000,
        detected_language="en",
    )

    class _FakeDb:
        async def get(self, model, key):
            return source_task

        async def execute(self, stmt):
            return None

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    session = {
        "quote_id": "q1",
        "task_uuid": "asr-task",
        "workflow_type": "transcribe_only",
        "file_id": "F1",
        "download_url": "https://files.example/clip.mp4",
        "file_name": "clip.mp4",
        "duration_ms": 90_000,
        "approved_source_srt_file_id": "srt-approved",
        "target_languages": [],
    }

    with (
        patch(
            "app.slack.media_configure_embed.AsyncSession",
            return_value=_FakeDb(),
        ),
        patch(
            "app.slack.media_configure_embed.create_asr_task",
            new_callable=AsyncMock,
            return_value="embed-task",
        ) as mock_create,
        patch("app.slack.media_configure_embed.httpx.AsyncClient"),
    ):
        await resume_configure_embed_phase(session=session, translated=False)

    asr_task = mock_create.await_args.args[0]
    assert asr_task.extra_data["duration_ms"] == 90_000


@pytest.mark.asyncio
async def test_accept_media_quote_copies_word_transcript_format_into_asr_extra_data():
    from app.slack.media_quote_actions import accept_media_quote

    client = AsyncMock()
    client.token = "xoxb-test"
    ray = MagicMock()
    ray.super_group = None
    ray.client.id = "client-1"
    context = MagicMock()
    context.__getitem__ = lambda self, key: {
        "user_id": "U1",
        "team_id": "T1",
    }.get(key)
    context.enterprise_id = None
    context.user_info = None
    context.get = lambda key, default=None: {"ray": ray}.get(key, default)

    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
        "pipeline_kind": PIPELINE_TRANSCRIBE,
        "total_tokens": 100,
        "channel_id": "C1",
        "thread_ts": None,
        "workflow_type": "transcribe_only",
        "embed_source": False,
        "embed_translated": False,
        "target_languages": [],
        "file_name": "clip.mp4",
        "file_id": "F1",
        "download_url": "https://example.com/clip.mp4",
        "duration_ms": 60_000,
        "word_transcript_format": "speakers",
    }

    with (
        patch(
            "app.slack.media_quote_actions.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch("app.slack.media_quote_actions.redis_conn") as mock_redis,
        patch(
            "app.slack.media_quote_actions._require_ai_token_balance",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.slack.media_quote_actions.require_ray_client",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.slack.media_quote_actions.resolve_slack_poster_email",
            new_callable=AsyncMock,
            return_value="user@example.com",
        ),
        patch(
            "app.slack.media_quote_actions.create_asr_task",
            new_callable=AsyncMock,
            return_value="task-1",
        ) as mock_create_asr,
        patch(
            "app.slack.media_workflow_actions.execute_media_workflow_decision",
            new_callable=AsyncMock,
            return_value=session,
        ),
        patch(
            "app.slack.media_quote_actions._update_quote_message",
            new_callable=AsyncMock,
        ),
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        started = await accept_media_quote(
            client=client,
            body={"channel": {"id": "C1"}, "message": {"ts": "1.2"}},
            action={"value": "q1"},
            context=context,
        )

    assert started is True
    mock_create_asr.assert_awaited_once()
    asr_task = mock_create_asr.await_args.args[0]
    assert asr_task.extra_data["word_transcript_format"] == "speakers"


@pytest.mark.asyncio
async def test_quote_admin_can_accept_another_users_media_quote():
    from app.slack.media_quote_actions import accept_media_quote

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {
        "user_id": "U-admin",
        "team_id": "T1",
    }.get(key)
    context.enterprise_id = None
    context.get = lambda key, default=None: None

    session = {
        "quote_id": "q1",
        "user_id": "U-owner",
        "stage": STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
        "pipeline_kind": PIPELINE_TRANSCRIBE,
        "total_tokens": 100,
        "channel_id": "C1",
        "workflow_type": "transcribe_only",
        "embed_source": False,
        "embed_translated": False,
        "target_languages": [],
        "file_name": "clip.mp4",
        "file_id": "F1",
        "download_url": "https://example.com/clip.mp4",
        "duration_ms": 60_000,
    }

    with (
        patch(
            "app.slack.media_quote_actions.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.slack.media_quotes.user_may_receive_quotes",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.slack.middleware.populate_ray_connection",
            new_callable=AsyncMock,
        ),
        patch("app.slack.media_quote_actions.redis_conn") as mock_redis,
        patch(
            "app.slack.media_quote_actions._require_ai_token_balance",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.slack.media_quote_actions.require_ray_client",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.slack.media_quote_actions.resolve_slack_poster_email",
            new_callable=AsyncMock,
            return_value="admin@example.com",
        ),
        patch(
            "app.slack.media_quote_actions._create_asr_from_quote_session",
            new_callable=AsyncMock,
            return_value="task-1",
        ),
        patch(
            "app.slack.media_workflow_actions.execute_media_workflow_decision",
            new_callable=AsyncMock,
            return_value=session,
        ),
        patch(
            "app.slack.media_quote_actions._update_quote_message",
            new_callable=AsyncMock,
        ),
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        await accept_media_quote(
            client=client,
            body={"channel": {"id": "C1"}, "message": {"ts": "1.2"}},
            action={"value": "q1"},
            context=context,
        )

    mock_redis.set.assert_awaited()
    texts = [
        str(call.kwargs.get("text") or "")
        for call in client.chat_postMessage.await_args_list
    ]
    assert all("permission" not in text.lower() for text in texts)


@pytest.mark.asyncio
async def test_quote_admin_can_accept_another_users_translation_quote():
    from app.slack.media_quote_actions import accept_media_translation_quote

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {
        "user_id": "U-admin",
        "team_id": "T1",
    }.get(key)
    context.enterprise_id = None
    context.get = lambda key, default=None: None

    session = {
        "quote_id": "q1",
        "user_id": "U-owner",
        "stage": STAGE_AWAITING_TRANSLATION_ACCEPT,
        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
        "total_tokens": 2,
        "channel_id": "C1",
        "task_uuid": "task-1",
        "target_languages": ["es"],
        "thread_ts": None,
    }

    with (
        patch(
            "app.slack.media_quote_actions.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.slack.media_quotes.user_may_receive_quotes",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.slack.middleware.populate_ray_connection",
            new_callable=AsyncMock,
        ),
        patch("app.slack.media_quote_actions.redis_conn") as mock_redis,
        patch(
            "app.slack.media_quote_actions._require_ai_token_balance",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.slack.media_quote_actions._resume_translate_phase",
            new_callable=AsyncMock,
        ),
        patch(
            "app.slack.media_quote_actions.update_media_quote_session",
            new_callable=AsyncMock,
            return_value={**session, "stage": "translating"},
        ),
        patch(
            "app.slack.media_quote_actions._update_quote_message",
            new_callable=AsyncMock,
        ),
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        await accept_media_translation_quote(
            client=client,
            body={"channel": {"id": "C1"}, "message": {"ts": "1.2"}},
            action={"value": "q1"},
            context=context,
        )

    mock_redis.set.assert_awaited()
    texts = [
        str(call.kwargs.get("text") or "")
        for call in client.chat_postMessage.await_args_list
    ]
    assert all("permission" not in text.lower() for text in texts)


@pytest.mark.asyncio
async def test_resume_translate_uses_replaced_source_srt():
    from app.slack.media_quote_actions import _resume_translate_phase

    captured = []
    source_task = SimpleNamespace(extra_data={})

    class _FakeDb:
        async def get(self, model, key):
            return source_task

        async def execute(self, stmt):
            captured.append(stmt)

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    session = {
        "quote_id": "q1",
        "workflow_type": "transcribe_translate",
        "embed_source": False,
        "embed_translated": True,
        "approved_source_srt_file_id": "srt-replaced",
        "target_languages": ["es"],
    }

    with (
        patch(
            "app.slack.media_quote_actions.AsyncSession",
            return_value=_FakeDb(),
        ),
        patch("app.slack.media_quote_actions.httpx.AsyncClient") as mock_http,
    ):
        mock_http.return_value.__aenter__.return_value.post = AsyncMock()
        await _resume_translate_phase(
            task_uuid="asr-task",
            pipeline_kind=PIPELINE_TRANSCRIBE_TRANSLATE,
            session=session,
        )

    compiled = captured[0].compile()
    assert compiled.params.get("result_file_id") == "srt-replaced"


def test_media_transcribe_wait_text_uses_file_plural():
    from app.slack.media_quote_actions import media_transcribe_wait_text
    from app.slack.media_quotes import (
        PIPELINE_TRANSCRIBE_TRANSLATE_EMBED,
    )

    assert "file(s)" in media_transcribe_wait_text(PIPELINE_TRANSCRIBE)
    assert "AI Translation quote" in media_transcribe_wait_text(
        PIPELINE_TRANSCRIBE_TRANSLATE
    )
    assert "AI Translation quote" in media_transcribe_wait_text(
        PIPELINE_TRANSCRIBE_TRANSLATE_EMBED
    )
