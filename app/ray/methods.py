from slack_sdk.web.async_client import AsyncWebClient
from ray_sdk import RayAuthError
from ray_sdk.api.v3.models import Job, Language

from .auth import get_ray_service
from ..slack import web
from ..slack.templates.models import NewJobForm
from ..config import config


async def get_languages() -> list[Language]:
    ray = get_ray_service()
    return await ray.get_languages()


async def get_job(access_token: str, job_id: str) -> Job | None:
    ray = get_ray_service(access_token)
    try:
        return await ray.get_job(job_id)
    except RayAuthError:
        return None


async def submit_job(
    client_id: str, access_token: str, client: AsyncWebClient, form: NewJobForm
):
    ray = get_ray_service(access_token)
    file_ids = (file.id for file in form.files if file.id)
    # TODO: check if file is downloaded
    file_paths = await web.download_files(client, file_ids)
    await ray.new_job(
        file_path=file_paths[0],
        title=form.reference or "Slack job",
        sl=form.source_lang.code,
        tl=[lang.code for lang in form.target_langs],
        workflow=form.workflow,
        callback_uri=f"{config.base_url}/ray/callback?client_id={client_id}",
        additional_data={"app_source": "slack"},
    )
