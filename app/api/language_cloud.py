import httpx

from app.config import domains
from app.auth.connector import RayClient
from pydantic import BaseModel


class DetectLanguageResponse(BaseModel):
    language: str
    confidence: float


async def detect_language(ray_client: RayClient, text: str) -> DetectLanguageResponse:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{domains.languagecloud_api}/mt/detect",
            json={"text": text},
            headers={"Authorization": f"Bearer {ray_client.id_token}"},
        )
    response.raise_for_status()
    return DetectLanguageResponse(**response.json())
