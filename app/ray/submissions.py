import hashlib
import os
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engines
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
    """
    file_hash = _hash_file_content_sha256_hex(path)
    file_size = _get_file_size(path)

    with Session(engines["ray_integration"]) as session:
        existing = _find_existing(
            session,
            user_id=user_id,
            team_id=team_id,
            file_hash=file_hash,
            file_name=file_name,
            target_language=target_language,
        )
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
        )
        return False, created
