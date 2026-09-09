from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class MediaWorkflowStage(StrEnum):
    AWAITING_TRANSCRIPTION_ACCEPT = "awaiting_transcription_accept"
    TRANSCRIBING = "transcribing"
    AWAITING_SOURCE_REVIEW = "awaiting_source_review"
    EMBEDDING_SOURCE = "embedding_source"
    AWAITING_TRANSLATION_ACCEPT = "awaiting_translation_accept"
    TRANSLATING = "translating"
    AWAITING_TRANSLATION_REVIEW = "awaiting_translation_review"
    EMBEDDING_TRANSLATED = "embedding_translated"
    DONE = "done"
    CANCELLED = "cancelled"


class MediaWorkflowType(StrEnum):
    TRANSCRIBE_ONLY = "transcribe_only"
    TRANSCRIBE_TRANSLATE = "transcribe_translate"


class MediaWorkflowEvent(StrEnum):
    QUOTE1_ACCEPTED = "quote1_accepted"
    QUOTE1_CANCELLED = "quote1_cancelled"
    TRANSCRIPTION_COMPLETED = "transcription_completed"
    SOURCE_SRT_APPROVED = "source_srt_approved"
    SOURCE_SRT_REPLACED = "source_srt_replaced"
    SOURCE_EMBED_COMPLETED = "source_embed_completed"
    QUOTE2_ACCEPTED = "quote2_accepted"
    QUOTE2_CANCELLED = "quote2_cancelled"
    TRANSLATION_COMPLETED = "translation_completed"
    TRANSLATED_SRT_APPROVED = "translated_srt_approved"
    TRANSLATED_SRT_REPLACED = "translated_srt_replaced"
    TRANSLATED_EMBED_COMPLETED = "translated_embed_completed"


class MediaWorkflowCommand(StrEnum):
    START_TRANSCRIBE = "start_transcribe"
    POST_SOURCE_REVIEW = "post_source_review"
    START_SOURCE_EMBED = "start_source_embed"
    POST_QUOTE2 = "post_quote2"
    START_TRANSLATE = "start_translate"
    POST_TRANSLATION_REVIEW = "post_translation_review"
    START_TRANSLATED_EMBED = "start_translated_embed"
    MARK_DONE = "mark_done"


class MediaEmbedRole(StrEnum):
    SOURCE = "source"
    TRANSLATED = "translated"


class MediaWorkflowTransitionError(Exception):
    """Raised when an event is not legal for the current media workflow stage."""

    def __init__(self, stage: MediaWorkflowStage, event: MediaWorkflowEvent) -> None:
        super().__init__(f"Media workflow cannot handle {event} while in stage {stage}")
        self.stage = stage
        self.event = event


@dataclass(frozen=True)
class MediaWorkflowConfig:
    workflow_type: MediaWorkflowType
    embed_source: bool
    embed_translated: bool
    review_gate: bool
    target_languages: tuple[str, ...] = ()


@dataclass(frozen=True)
class MediaWorkflowSession:
    stage: MediaWorkflowStage
    config: MediaWorkflowConfig


@dataclass(frozen=True)
class MediaWorkflowDecision:
    session: MediaWorkflowSession
    commands: tuple[MediaWorkflowCommand, ...]


def make_media_workflow_session(
    *,
    workflow_type: MediaWorkflowType,
    embed_source: bool,
    embed_translated: bool,
    review_gate: bool,
    target_languages: tuple[str, ...] = (),
) -> MediaWorkflowSession:
    return MediaWorkflowSession(
        stage=MediaWorkflowStage.AWAITING_TRANSCRIPTION_ACCEPT,
        config=MediaWorkflowConfig(
            workflow_type=workflow_type,
            embed_source=embed_source,
            embed_translated=embed_translated,
            review_gate=review_gate,
            target_languages=target_languages,
        ),
    )


def media_workflow_session_from_quote(
    session: dict[str, Any],
) -> MediaWorkflowSession | None:
    raw_type = session.get("workflow_type")
    if not raw_type:
        return None
    stage_value = session.get("workflow_stage") or session.get("stage")
    return MediaWorkflowSession(
        stage=MediaWorkflowStage(str(stage_value)),
        config=MediaWorkflowConfig(
            workflow_type=MediaWorkflowType(str(raw_type)),
            embed_source=bool(session.get("embed_source")),
            embed_translated=bool(session.get("embed_translated")),
            review_gate=bool(session.get("review_gate", True)),
            target_languages=tuple(session.get("target_languages") or ()),
        ),
    )


def _session_in(
    session: MediaWorkflowSession, stage: MediaWorkflowStage
) -> MediaWorkflowSession:
    return MediaWorkflowSession(stage=stage, config=session.config)


def _is_translate(session: MediaWorkflowSession) -> bool:
    return session.config.workflow_type is MediaWorkflowType.TRANSCRIBE_TRANSLATE


def _after_source_approved(session: MediaWorkflowSession) -> MediaWorkflowDecision:
    if _is_translate(session):
        commands: tuple[MediaWorkflowCommand, ...] = (
            (MediaWorkflowCommand.START_SOURCE_EMBED, MediaWorkflowCommand.POST_QUOTE2)
            if session.config.embed_source
            else (MediaWorkflowCommand.POST_QUOTE2,)
        )
        return MediaWorkflowDecision(
            session=_session_in(
                session, MediaWorkflowStage.AWAITING_TRANSLATION_ACCEPT
            ),
            commands=commands,
        )
    if session.config.embed_source:
        return MediaWorkflowDecision(
            session=_session_in(session, MediaWorkflowStage.EMBEDDING_SOURCE),
            commands=(MediaWorkflowCommand.START_SOURCE_EMBED,),
        )
    return MediaWorkflowDecision(
        session=_session_in(session, MediaWorkflowStage.DONE),
        commands=(MediaWorkflowCommand.MARK_DONE,),
    )


def _after_translated_approved(session: MediaWorkflowSession) -> MediaWorkflowDecision:
    if session.config.embed_translated:
        return MediaWorkflowDecision(
            session=_session_in(session, MediaWorkflowStage.EMBEDDING_TRANSLATED),
            commands=(MediaWorkflowCommand.START_TRANSLATED_EMBED,),
        )
    return MediaWorkflowDecision(
        session=_session_in(session, MediaWorkflowStage.DONE),
        commands=(MediaWorkflowCommand.MARK_DONE,),
    )


def advance_media_workflow(
    session: MediaWorkflowSession,
    event: MediaWorkflowEvent,
) -> MediaWorkflowDecision:
    stage = session.stage
    if (
        event is MediaWorkflowEvent.QUOTE1_ACCEPTED
        and stage is MediaWorkflowStage.AWAITING_TRANSCRIPTION_ACCEPT
    ):
        return MediaWorkflowDecision(
            session=_session_in(session, MediaWorkflowStage.TRANSCRIBING),
            commands=(MediaWorkflowCommand.START_TRANSCRIBE,),
        )
    if (
        event is MediaWorkflowEvent.QUOTE1_CANCELLED
        and stage is MediaWorkflowStage.AWAITING_TRANSCRIPTION_ACCEPT
    ):
        return MediaWorkflowDecision(
            session=_session_in(session, MediaWorkflowStage.CANCELLED),
            commands=(),
        )
    if (
        event is MediaWorkflowEvent.TRANSCRIPTION_COMPLETED
        and stage is MediaWorkflowStage.TRANSCRIBING
    ):
        if session.config.review_gate:
            return MediaWorkflowDecision(
                session=_session_in(session, MediaWorkflowStage.AWAITING_SOURCE_REVIEW),
                commands=(MediaWorkflowCommand.POST_SOURCE_REVIEW,),
            )
        return _after_source_approved(session)
    if (
        event is MediaWorkflowEvent.SOURCE_SRT_REPLACED
        and stage is MediaWorkflowStage.AWAITING_SOURCE_REVIEW
    ):
        return MediaWorkflowDecision(session=session, commands=())
    if (
        event is MediaWorkflowEvent.SOURCE_SRT_APPROVED
        and stage is MediaWorkflowStage.AWAITING_SOURCE_REVIEW
    ):
        return _after_source_approved(session)
    if (
        event is MediaWorkflowEvent.SOURCE_EMBED_COMPLETED
        and stage is MediaWorkflowStage.EMBEDDING_SOURCE
    ):
        return MediaWorkflowDecision(
            session=_session_in(session, MediaWorkflowStage.DONE),
            commands=(MediaWorkflowCommand.MARK_DONE,),
        )
    if event is MediaWorkflowEvent.SOURCE_EMBED_COMPLETED and stage in (
        MediaWorkflowStage.AWAITING_TRANSLATION_ACCEPT,
        MediaWorkflowStage.TRANSLATING,
        MediaWorkflowStage.AWAITING_TRANSLATION_REVIEW,
        MediaWorkflowStage.EMBEDDING_TRANSLATED,
    ):
        return MediaWorkflowDecision(session=session, commands=())
    if (
        event is MediaWorkflowEvent.QUOTE2_ACCEPTED
        and stage is MediaWorkflowStage.AWAITING_TRANSLATION_ACCEPT
    ):
        return MediaWorkflowDecision(
            session=_session_in(session, MediaWorkflowStage.TRANSLATING),
            commands=(MediaWorkflowCommand.START_TRANSLATE,),
        )
    if (
        event is MediaWorkflowEvent.QUOTE2_CANCELLED
        and stage is MediaWorkflowStage.AWAITING_TRANSLATION_ACCEPT
    ):
        return MediaWorkflowDecision(
            session=_session_in(session, MediaWorkflowStage.CANCELLED),
            commands=(),
        )
    if (
        event is MediaWorkflowEvent.TRANSLATION_COMPLETED
        and stage is MediaWorkflowStage.TRANSLATING
    ):
        if session.config.review_gate:
            return MediaWorkflowDecision(
                session=_session_in(
                    session, MediaWorkflowStage.AWAITING_TRANSLATION_REVIEW
                ),
                commands=(MediaWorkflowCommand.POST_TRANSLATION_REVIEW,),
            )
        return _after_translated_approved(session)
    if (
        event is MediaWorkflowEvent.TRANSLATED_SRT_REPLACED
        and stage is MediaWorkflowStage.AWAITING_TRANSLATION_REVIEW
    ):
        return MediaWorkflowDecision(session=session, commands=())
    if (
        event is MediaWorkflowEvent.TRANSLATED_SRT_APPROVED
        and stage is MediaWorkflowStage.AWAITING_TRANSLATION_REVIEW
    ):
        return _after_translated_approved(session)
    if (
        event is MediaWorkflowEvent.TRANSLATED_EMBED_COMPLETED
        and stage is MediaWorkflowStage.EMBEDDING_TRANSLATED
    ):
        return MediaWorkflowDecision(
            session=_session_in(session, MediaWorkflowStage.DONE),
            commands=(MediaWorkflowCommand.MARK_DONE,),
        )
    raise MediaWorkflowTransitionError(stage, event)
