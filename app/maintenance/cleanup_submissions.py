from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.database import engines
from app.models import SlackFileTranslationSubmission


def cleanup_submissions(max_age: timedelta) -> int:
    """Delete records older than max_age from SlackFileTranslationSubmission.

    Returns the number of rows deleted.
    """
    cutoff = datetime.now(timezone.utc) - max_age
    with Session(engines["ray_integration"]) as session:
        result = session.execute(
            delete(SlackFileTranslationSubmission).where(
                SlackFileTranslationSubmission.created_at < cutoff
            )
        )
        session.commit()
        return result.rowcount or 0
