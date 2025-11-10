import httpx
from pydantic import BaseModel
from slack_bolt.context.async_context import AsyncBoltContext
from straker_auth.languagecloud.jwt import create_languagecloud_group_token

from app.config import config, domains
from app.slack.buglog_notifier import notify_exception


class DetectLanguageResponse(BaseModel):
    language: str
    confidence: float


async def detect_language(
    context: AsyncBoltContext, text: str
) -> DetectLanguageResponse:
    token = (
        context["ray"].client.id_token
        if context["ray"].client
        else create_languagecloud_group_token(
            context["ray"].super_group[0].verify_organization_uuid,
            aud="languagecloud-api",
            secret=config.languagecloud_api_key.get_secret_value(),
        )
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{domains.languagecloud_api}/mt/detect",
                json={"text": text},
                headers={"Authorization": f"Bearer {token}"},
            )
        response.raise_for_status()
        return DetectLanguageResponse(**response.json())
    except httpx.HTTPStatusError as e:
        notify_exception(msg="Error detecting language", exc=e)
        raise
    except Exception as e:
        notify_exception(
            msg="An unexpected error occurred during language detection", exc=e
        )
        raise
