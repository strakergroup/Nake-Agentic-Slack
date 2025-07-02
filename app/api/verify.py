from io import BytesIO
import json
import os
from buglog import notify_exception
import httpx
from typing import List
from ..redis import redis_conn

from app.auth.connector import RayClient, SlackUser, get_ray_client
from app.config import domains, config
from straker_utils.environment import Environment


from app.ray.utils import get_filename_from_header


async def submit_evaluation_job(
    user: RayClient,
    file_path: list[str],
    target_languages_uuid: List[str],
    reference: str,
    workflow_uuid: str | None = None,
    job_notes: str = "",
):
    # Prepare the data for the request
    target_languages_data = {
        "target_languages": target_languages_uuid,
        "title": reference,
        "source": "slack",
    }
    if job_notes:
        target_languages_data["client_notes"] = job_notes
    if not workflow_uuid and (
        config.environment != Environment.production
        or domains.slack_ray_translator
        == "https://stage-slack-deltaray.strakertranslations.com"
    ):
        target_languages_data["workflow"] = "ff9d336e-4043-41cd-bd95-0d65a5eeb945"
    else:
        target_languages_data["workflow"] = workflow_uuid

    # Create a job using streaming for file uploads
    async with httpx.AsyncClient(timeout=300) as client:
        # Create a multipart form with streaming files
        files = [("files", open(file, "rb")) for file in file_path]

        try:
            response = await client.post(
                f"{domains.verify_api}/evaluate/create",
                files=files,
                data=target_languages_data,
                headers={"Authorization": f"Bearer {user.id_token}"},
            )
            response.raise_for_status()
            return response.json()
        finally:
            # Ensure all file handles are closed
            for _, file_obj in files:
                file_obj.close()


async def get_evaluation_job(user: SlackUser, job_uuid: str):
    ray_client = await get_ray_client(user.user_id, user.team_id, user.enterprise_id)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(  # Added missing await
            f"{domains.verify_api}/evaluate/{job_uuid}",
            headers={"Authorization": f"Bearer {ray_client.id_token}"},
        )
    response.raise_for_status()
    return response.json()


async def get_client_evaluation_job(ray_client: RayClient, job_uuid: str):
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(  # Added missing await
            f"{domains.verify_api}/evaluate/{job_uuid}",
            headers={"Authorization": f"Bearer {ray_client.id_token}"},
        )
    response.raise_for_status()
    return response.json()


async def download_verify_file(ray_client: RayClient, file_uuid: str):
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(  # Added missing await
            f"{domains.verify_api}/files/{file_uuid}",
            headers={"Authorization": f"Bearer {ray_client.id_token}"},
        )
    response.raise_for_status()
    content_disposition = response.headers.get("Content-Disposition")

    # Parse the header to get the filename
    filename = get_filename_from_header(content_disposition)
    file_result = {
        "file_name": filename,
        "file": BytesIO(response.content),
    }
    return file_result


async def create_human_job(
    ray_client: RayClient,
    job_uuid: str,
    file_and_languages: List[str],
):
    """
    Create a human job in the Verify API

    Args:
        ray_client: RayClient object
        job_uuid: UUID of the job
        file_and_languages: List of strings with the format "file_uuid:language_uuid"
    """
    url = f"{domains.verify_api}/automation/service/create-human-job"
    headers = {"Authorization": f"Bearer {ray_client.id_token}"}
    # TODO: allow submission
    service_uuid = "37f2e44b-ba3c-42b1-83c7-d3023298292f"
    # TODO: What is this?
    purchase_order_number = "123456"
    data = {
        "job_uuid": job_uuid,
        "service_uuid": service_uuid,
        "file_and_languages": file_and_languages,
        "purchase_order_number": purchase_order_number,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.post(url, headers=headers, data=data)

    response.raise_for_status()
    return response.json()


async def get_verify_languages():
    key = "slack-ray-translator:verify:languages"
    cached = ""
    try:
        cached = await redis_conn.get(key)
    except Exception as e:
        notify_exception(e)
    if cached:
        try:
            languages = json.loads(cached)
            assert isinstance(languages, list)
            return languages
        except Exception as e:
            notify_exception(e)

    url = f"{domains.verify_api}/languages"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url)
    response.raise_for_status()
    languages = response.json()["data"]

    languages = [
        {"code": lang["code"], "name": lang["name"], "uuid": lang["uuid"]}
        for lang in languages
    ]
    # Cache languages for 1 hour.
    try:
        await redis_conn.set(key, json.dumps(languages), ex=3600)
    except Exception as e:
        notify_exception(e)
    return languages


async def get_job_pricing(
    ray_client: RayClient,
    job_uuid: str,
    file_uuids: list[str],
    language_uuids: list[str],
):
    url = f"{domains.verify_api}/automation/service/pricing"
    headers = {"Authorization": f"Bearer {ray_client.id_token}"}
    # TODO: Update for multiple files
    file_and_languages = [
        f"{file_uuid}:{lang}" for lang in language_uuids for file_uuid in file_uuids
    ]
    data = {"job_uuid": job_uuid, "file_and_languages": file_and_languages}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, data=data)
    response.raise_for_status()
    return response.json()
