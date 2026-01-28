import httpx
from pydantic import BaseModel
from slack_bolt.context.async_context import AsyncBoltContext
from straker_auth.languagecloud.jwt import create_languagecloud_group_token

from app.api.http_client import INTERNAL_SERVICE_TIMEOUT, retry_on_timeout
from app.config import config, domains


def _get_notify_exception():
    """Lazily import notify_exception to avoid circular imports."""
    from app.slack.buglog_notifier import notify_exception

    return notify_exception


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

        async def _detect_request():
            async with httpx.AsyncClient(timeout=INTERNAL_SERVICE_TIMEOUT) as client:
                response = await client.post(
                    f"{domains.languagecloud_api}/mt/detect",
                    json={"text": text},
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                return DetectLanguageResponse(**response.json())

        return await retry_on_timeout(
            _detect_request,
            notify_on_final_failure=False,  # We handle notification below
        )
    except httpx.HTTPStatusError as e:
        # Extract detailed error message from API response if available
        error_detail = None
        try:
            if e.response is not None:
                error_data = e.response.json()
                error_detail = error_data.get("detail", str(e))
        except Exception:
            error_detail = str(e)

        notify_exception = _get_notify_exception()
        notify_exception(
            msg=f"Error detecting language: {error_detail}",
            exc=e,
            extra={
                "status_code": e.response.status_code if e.response else None,
                "text_length": len(text),
            },
        )
        raise
    except Exception as e:
        notify_exception = _get_notify_exception()
        notify_exception(
            msg="An unexpected error occurred during language detection", exc=e
        )
        raise
