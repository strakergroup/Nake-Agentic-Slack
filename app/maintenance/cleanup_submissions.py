from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.database import engines
from app.models import SlackFileTranslationSubmission


def cleanup_submissions(max_age: timedelta) -> int:
    """Mark records older than max_age as deleted in SlackFileTranslationSubmission.

    Returns the number of rows marked as deleted.
    """
    cutoff = datetime.now(timezone.utc) - max_age
    with Session(engines["ray_integration"]) as session:
        result = session.execute(
            update(SlackFileTranslationSubmission)
            .where(SlackFileTranslationSubmission.created_at < cutoff)
            .where(SlackFileTranslationSubmission.is_deleted.is_(False))
            .values(is_deleted=True, deleted_at=datetime.now(timezone.utc))
        )
        session.commit()
        return getattr(result, "rowcount", 0) or 0
