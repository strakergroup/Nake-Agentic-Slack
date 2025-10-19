import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session
from straker_utils.sql.async_engine import execute, fetch_one

from app.database import async_engines
from app.models import SlackFileTranslationSubmission


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
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


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

    # Use async engine for database operations
    from sqlalchemy import text

    sql = text(
        """
        SELECT * FROM slack_file_translation_submission
        WHERE user_id = :user_id
        AND team_id = :team_id
        AND file_hash = :file_hash
        AND file_name = :file_name
        AND target_language = :target_language
        AND created_at >= :cutoff
        LIMIT 1
        """
    ).bindparams(
        user_id=user_id,
        team_id=team_id,
        file_hash=file_hash,
        file_name=file_name,
        target_language=target_language,
        cutoff=cutoff,
    )

    existing = await fetch_one(sql, async_engines["ray_integration"])

    if existing is not None:
        # Convert dict result to model instance
        existing_obj = SlackFileTranslationSubmission(
            id=existing.get("id"),
            user_id=existing.get("user_id"),
            team_id=existing.get("team_id"),
            channel_id=existing.get("channel_id"),
            file_hash=existing.get("file_hash"),
            file_name=existing.get("file_name"),
            file_size=existing.get("file_size"),
            target_language=existing.get("target_language"),
            file_id=existing.get("file_id"),
            created_at=existing.get("created_at"),
        )
        return True, existing_obj

    # Insert new submission using async engine
    insert_sql = text(
        """
        INSERT INTO slack_file_translation_submission
        (user_id, team_id, channel_id, file_hash, file_name, file_size, target_language, file_id, created_at)
        VALUES
        (:user_id, :team_id, :channel_id, :file_hash, :file_name, :file_size, :target_language, :file_id, NOW())
        """
    ).bindparams(
        user_id=user_id,
        team_id=team_id,
        channel_id=channel_id,
        file_hash=file_hash,
        file_name=file_name,
        file_size=file_size,
        target_language=target_language,
        file_id=file_id,
    )

    await execute(insert_sql, async_engines["ray_integration"], commit_after=True)

    # Get the created record
    created_sql = text(
        """
        SELECT * FROM slack_file_translation_submission
        WHERE user_id = :user_id
        AND team_id = :team_id
        AND file_hash = :file_hash
        AND file_name = :file_name
        AND target_language = :target_language
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).bindparams(
        user_id=user_id,
        team_id=team_id,
        file_hash=file_hash,
        file_name=file_name,
        target_language=target_language,
    )

    created_result = await fetch_one(created_sql, async_engines["ray_integration"])
    if not created_result:
        raise Exception("Failed to create submission record")
    created_obj = SlackFileTranslationSubmission(
        id=created_result.get("id"),
        user_id=created_result.get("user_id"),
        team_id=created_result.get("team_id"),
        channel_id=created_result.get("channel_id"),
        file_hash=created_result.get("file_hash"),
        file_name=created_result.get("file_name"),
        file_size=created_result.get("file_size"),
        target_language=created_result.get("target_language"),
        file_id=created_result.get("file_id"),
        created_at=created_result.get("created_at"),
    )

    return False, created_obj
