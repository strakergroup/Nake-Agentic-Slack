from ray_sdk import RayAuthError
from ray_sdk.api.v3.models import Job, Language
from .auth import get_ray_service


async def get_languages() -> list[Language]:
    ray = get_ray_service()
    return await ray.get_languages()


async def get_job(access_token: str, job_id: str) -> Job | None:
    ray = get_ray_service(access_token)
    try:
        return await ray.get_job(job_id)
    except RayAuthError:
        return None
