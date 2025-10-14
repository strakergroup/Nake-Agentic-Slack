"""Google API usage logging for the Slack app."""

from sqlalchemy.orm import Session

from app.database import engines
from app.models import GoogleApiLog


async def log_google_api_usage(
    user_uuid: str | None,  # LC UUID or Slack user_id
    group_uuid: str | None,
    organization_uuid: str,
    text: str,
    source_lang: str,
    translations: dict[str, str],
    transaction_uuid: str,
    email: str | None = None,
    app_name: str | None = None,
    usage_type: str = "direct_machine_translation",
    text_length: int = 0,
    word_count: int = 0,
    channel_name: str | None = None,
    gridfs_file_id: str | None = None,
) -> None:
    if not text_length:
        text_length = len(text)
    if not word_count:
        word_count = len(text.split())
    with Session(engines["ray_integration_log"]) as session:
        for target_lang, target_text in translations.items():
            session.add(
                GoogleApiLog(
                    user_uuid=user_uuid or "",
                    group_uuid=group_uuid or "",
                    super_group_uuid="",
                    verify_organization_uuid=organization_uuid,
                    app_name=app_name,
                    sl=source_lang,
                    tl=target_lang,
                    source_text=text,
                    target_text=target_text,
                    response=None,
                    word_count=word_count,
                    character_count=text_length,
                    email=email,
                    usage_type=usage_type,
                    transaction_uuid=transaction_uuid,
                    channel_name=channel_name,
                    gridfs_file_id=gridfs_file_id,
                )
            )
        session.commit()
