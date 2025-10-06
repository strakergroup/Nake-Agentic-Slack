import httpx
import buglog

from app.config import config, domains
from app.mt.translate import create_languagecloud_group_token
from slack_bolt.context.async_context import AsyncBoltContext
from pydantic import BaseModel


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
        buglog.notify_exception(msg="Error detecting language", exc=e)
        raise
    except Exception as e:
        buglog.notify_exception(
            msg="An unexpected error occurred during language detection", exc=e
        )
        raise
