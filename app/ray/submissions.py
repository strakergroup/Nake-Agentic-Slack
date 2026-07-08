import hashlib
import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.database import async_engines, engines
from app.models import SlackFileTranslationSubmission


class SubmissionStatus(Enum):
    CREATED = "created"
    FAILED = "failed"
    COMPLETED = "completed"


def _hash_file_content_sha256_hex(path: str, read_chunk_size: int = 1024 * 1024) -> str:
    """Exact SHA-256 of file content (no language prefix), streamed from disk."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(read_chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _get_file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def _find_existing(
    session: Session,
    *,
    user_id: str,
    team_id: str,
    file_hash: str,
    file_name: str,
    target_language: str,
) -> Optional[SlackFileTranslationSubmission]:
    stmt = (
        select(SlackFileTranslationSubmission)
        .where(SlackFileTranslationSubmission.user_id == user_id)
        .where(SlackFileTranslationSubmission.team_id == team_id)
        .where(SlackFileTranslationSubmission.file_hash == file_hash)
        .where(SlackFileTranslationSubmission.file_name == file_name)
        .where(SlackFileTranslationSubmission.target_language == target_language)
        .limit(1)
    )
    return session.scalars(stmt).first()


def _insert_submission(
    session: Session,
    *,
    user_id: str,
    team_id: str,
    channel_id: str,
    file_hash: str,
    file_name: str,
    file_size: int,
    source_language: str = "",
    target_language: str,
    file_id: str,
    processing_status: SubmissionStatus,
) -> SlackFileTranslationSubmission:
    record = SlackFileTranslationSubmission(
        user_id=user_id,
        team_id=team_id,
        channel_id=channel_id,
        file_hash=file_hash,
        file_name=file_name,
        file_size=file_size,
        source_language=source_language,
        target_language=target_language,
        file_id=file_id,
        processing_status=processing_status.value,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def updated_submission_status(
    *,
    submission_id: int,
    processing_status: SubmissionStatus,
) -> bool:
    """Update the status of an existing submission if present. Returns True when updated."""

    with Session(engines["ray_integration"]) as session:
        stmt = (
            select(SlackFileTranslationSubmission)
            .where(SlackFileTranslationSubmission.id == submission_id)
            .limit(1)
        )
        existing = session.scalars(stmt).first()

        if existing is None:
            return False

        existing.processing_status = processing_status.value
        session.commit()

        return True


async def check_and_record_submission_async(
    *,
    path: str,
    file_name: str,
    file_id: str,
    user_id: str,
    team_id: str,
    channel_id: str,
    source_language: str = "",
    target_language: str,
) -> Tuple[bool, SlackFileTranslationSubmission]:
    """
    Returns (is_duplicate, record). If duplicate, record is the existing one.
    Only checks for duplicates within the last 24 hours.
    """
    file_hash = _hash_file_content_sha256_hex(path)
    file_size = _get_file_size(path)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    with Session(engines["ray_integration"]) as session:
        existing = session.scalars(
            select(SlackFileTranslationSubmission)
            .where(SlackFileTranslationSubmission.user_id == user_id)
            .where(SlackFileTranslationSubmission.team_id == team_id)
            .where(SlackFileTranslationSubmission.file_hash == file_hash)
            .where(SlackFileTranslationSubmission.file_name == file_name)
            .where(SlackFileTranslationSubmission.source_language == source_language)
            .where(SlackFileTranslationSubmission.target_language == target_language)
            .where(SlackFileTranslationSubmission.created_at >= cutoff)
            .where(
                SlackFileTranslationSubmission.processing_status
                != SubmissionStatus.FAILED.value
            )
            .limit(1)
        ).first()

        if existing is not None:
            return True, existing

        created = _insert_submission(
            session,
            user_id=user_id,
            team_id=team_id,
            channel_id=channel_id,
            file_hash=file_hash,
            file_name=file_name,
            file_size=file_size,
            source_language=source_language,
            target_language=target_language,
            file_id=file_id,
            processing_status=SubmissionStatus.CREATED,
        )
        return False, created


async def check_and_record_submission_metadata_async(
    *,
    file_hash: str,
    file_name: str,
    file_size: int,
    file_id: str,
    user_id: str,
    team_id: str,
    channel_id: str,
    source_language: str = "",
    target_language: str,
) -> Tuple[bool, SlackFileTranslationSubmission]:
    """
    Same duplicate check as ``check_and_record_submission_async`` but uses file
    metadata captured during a prior preflight pass, avoiding a second file read.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    async with AsyncSession(
        async_engines["ray_integration"],
        expire_on_commit=False,
    ) as session:
        existing_result = await session.scalars(
            select(SlackFileTranslationSubmission)
            .where(SlackFileTranslationSubmission.user_id == user_id)
            .where(SlackFileTranslationSubmission.team_id == team_id)
            .where(SlackFileTranslationSubmission.file_hash == file_hash)
            .where(SlackFileTranslationSubmission.file_name == file_name)
            .where(SlackFileTranslationSubmission.source_language == source_language)
            .where(SlackFileTranslationSubmission.target_language == target_language)
            .where(SlackFileTranslationSubmission.created_at >= cutoff)
            .where(
                SlackFileTranslationSubmission.processing_status
                != SubmissionStatus.FAILED.value
            )
            .limit(1)
        )
        existing = existing_result.first()

        if existing is not None:
            return True, existing

        created = SlackFileTranslationSubmission(
            user_id=user_id,
            team_id=team_id,
            channel_id=channel_id,
            file_hash=file_hash,
            file_name=file_name,
            file_size=file_size,
            source_language=source_language,
            target_language=target_language,
            file_id=file_id,
            processing_status=SubmissionStatus.CREATED.value,
        )
        session.add(created)
        await session.commit()
        await session.refresh(created)
        return False, created


async def check_and_record_transcription_submission_async(
    *,
    slack_file_id: str,
    file_name: str,
    user_id: str,
    team_id: str,
    channel_id: str,
    target_language: str,
) -> Tuple[bool, SlackFileTranslationSubmission]:
    """
    Check for duplicate transcription+translation submissions using Slack file_id.
    Returns (is_duplicate, record). If duplicate, record is the existing one.
    Only checks for duplicates within the last 24 hours.

    This is used for transcribe+translate flow where we don't have the file
    content yet (only the Slack file_id).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    with Session(engines["ray_integration"]) as session:
        # Use slack_file_id as the file_hash for transcription submissions
        existing = session.scalars(
            select(SlackFileTranslationSubmission)
            .where(SlackFileTranslationSubmission.user_id == user_id)
            .where(SlackFileTranslationSubmission.team_id == team_id)
            .where(SlackFileTranslationSubmission.file_hash == slack_file_id)
            .where(SlackFileTranslationSubmission.file_name == file_name)
            .where(SlackFileTranslationSubmission.target_language == target_language)
            .where(SlackFileTranslationSubmission.created_at >= cutoff)
            .where(
                SlackFileTranslationSubmission.processing_status
                != SubmissionStatus.FAILED.value
            )
            .limit(1)
        ).first()

        if existing is not None:
            return True, existing

        created = _insert_submission(
            session,
            user_id=user_id,
            team_id=team_id,
            channel_id=channel_id,
            file_hash=slack_file_id,  # Use Slack file_id as hash
            file_name=file_name,
            file_size=0,  # Unknown at submission time
            target_language=target_language,
            file_id=slack_file_id,
            processing_status=SubmissionStatus.CREATED,
        )
        return False, created


async def check_and_record_direct_embed_submission_async(
    *,
    video_file_id: str,
    subtitle_file_id: str,
    file_name: str,
    user_id: str,
    team_id: str,
    channel_id: str,
) -> Tuple[bool, SlackFileTranslationSubmission]:
    """
    Check for duplicate direct embed submissions using video + subtitle Slack file IDs.

    Uses a stable SHA-256 hash in file_hash to keep within database column limits while
    still uniquely identifying the video/subtitle pair.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    dedupe_hash = hashlib.sha256(
        f"{video_file_id}:{subtitle_file_id}".encode("utf-8")
    ).hexdigest()

    with Session(engines["ray_integration"]) as session:
        existing = session.scalars(
            select(SlackFileTranslationSubmission)
            .where(SlackFileTranslationSubmission.user_id == user_id)
            .where(SlackFileTranslationSubmission.team_id == team_id)
            .where(SlackFileTranslationSubmission.file_hash == dedupe_hash)
            .where(SlackFileTranslationSubmission.file_name == file_name)
            .where(SlackFileTranslationSubmission.target_language == "embed")
            .where(SlackFileTranslationSubmission.created_at >= cutoff)
            .where(
                SlackFileTranslationSubmission.processing_status
                != SubmissionStatus.FAILED.value
            )
            .limit(1)
        ).first()

        if existing is not None:
            return True, existing

        created = _insert_submission(
            session,
            user_id=user_id,
            team_id=team_id,
            channel_id=channel_id,
            file_hash=dedupe_hash,
            file_name=file_name,
            # Embed-only submissions don't track file size; the video isn't
            # re-downloaded here so the actual size is unavailable.
            file_size=0,
            target_language="embed",
            file_id=video_file_id,
            processing_status=SubmissionStatus.CREATED,
        )
        return False, created


async def check_and_record_transcription_only_submission_async(
    *,
    slack_file_id: str,
    file_name: str,
    user_id: str,
    team_id: str,
    channel_id: str,
) -> Tuple[bool, SlackFileTranslationSubmission]:
    """
    Check for duplicate transcription-only submissions using Slack file_id.
    Returns (is_duplicate, record). If duplicate, record is the existing one.
    Only checks for duplicates within the last 24 hours.

    This is used for transcription-only flow (no translation) where we don't have
    the file content yet (only the Slack file_id). Uses empty string for target_language.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    with Session(engines["ray_integration"]) as session:
        # Use slack_file_id as the file_hash for transcription submissions
        # Use empty string for target_language to indicate transcription-only
        existing = session.scalars(
            select(SlackFileTranslationSubmission)
            .where(SlackFileTranslationSubmission.user_id == user_id)
            .where(SlackFileTranslationSubmission.team_id == team_id)
            .where(SlackFileTranslationSubmission.file_hash == slack_file_id)
            .where(SlackFileTranslationSubmission.file_name == file_name)
            .where(SlackFileTranslationSubmission.target_language == "")
            .where(SlackFileTranslationSubmission.created_at >= cutoff)
            .where(
                SlackFileTranslationSubmission.processing_status
                != SubmissionStatus.FAILED.value
            )
            .limit(1)
        ).first()

        if existing is not None:
            return True, existing

        created = _insert_submission(
            session,
            user_id=user_id,
            team_id=team_id,
            channel_id=channel_id,
            file_hash=slack_file_id,  # Use Slack file_id as hash
            file_name=file_name,
            file_size=0,  # Unknown at submission time
            target_language="",  # Empty string indicates transcription-only
            file_id=slack_file_id,
            processing_status=SubmissionStatus.CREATED,
        )
        return False, created
