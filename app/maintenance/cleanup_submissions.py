from datetime import datetime, timedelta, timezone

from straker_utils.sql.async_engine import execute

from app.database import async_engines


async def cleanup_submissions(max_age: timedelta) -> None:
    """Delete records older than max_age from SlackFileTranslationSubmission.

    Returns the number of rows deleted.
    """
    cutoff = datetime.now(timezone.utc) - max_age

    # Use async engine for database operations
    from sqlalchemy import text

    sql = text(
        """
        DELETE FROM slack_file_translation_submission
        WHERE created_at < :cutoff
        """
    ).bindparams(cutoff=cutoff)

    await execute(sql, async_engines["ray_integration"], commit_after=True)
    return None
