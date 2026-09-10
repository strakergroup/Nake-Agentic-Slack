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
    assert asr_task.extra_data["srt_file_ids"] == ["srt-replaced", "srt-fr"]
    assert asr_task.extra_data["language_codes"] == ["es", "fr"]
    assert asr_task.extra_data["target_languages"] == ["es", "fr"]
    mock_http.assert_not_called()
