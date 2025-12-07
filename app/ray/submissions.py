import hashlib
import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engines
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
            target_language=target_language,
            file_id=file_id,
            processing_status=SubmissionStatus.CREATED,
        )
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
