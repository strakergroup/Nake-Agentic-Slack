from io import BytesIO
import os
import httpx
from typing import List

from app import config
from app.auth.connector import RayClient, SlackUser, get_ray_client
from app.config import domains

from straker_auth.languagecloud import create_languagecloud_id_token

from app.ray.utils import get_filename_from_header


async def submit_evaluation_job(
    user: RayClient, file_path: str, target_languages_uuid: List[str], reference: str
):
    # Prepare the data for the request
    target_languages_data = {
        "target_languages": target_languages_uuid,
        "title": reference,
        "source": "slack",
    }
    files = {
        "files": (
            os.path.basename(file_path),
            open(file_path, "rb"),
            "application/octet-stream",
        )
    }
    # Create a job
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{domains.verify_api}/evaluate/create",
            files=files,
            data=target_languages_data,
            headers={"Authorization": f"Bearer {user.id_token}"},
        )
    # Check the response status
    response.raise_for_status()

    # Return the job response
    return response.json()


async def get_evaluation_job(user: SlackUser, job_uuid: str):
    ray_client = await get_ray_client(user.user_id, user.team_id, user.enterprise_id)
    with httpx.Client() as client:
        response = client.get(
            f"{domains.verify_api}/evaluate/{job_uuid}",
            headers={"Authorization": f"Bearer {ray_client.id_token}"},
        )
    response.raise_for_status()
    return response.json()


async def get_client_evaluation_job(ray_client: RayClient, job_uuid: str):
    with httpx.Client() as client:
        response = client.get(
            f"{domains.verify_api}/evaluate/{job_uuid}",
            headers={"Authorization": f"Bearer {ray_client.id_token}"},
        )
    response.raise_for_status()
    return response.json()


async def download_verify_file(ray_client: RayClient, file_uuid: str):
    with httpx.Client() as client:
        response = client.get(
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

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, data=data)

    response.raise_for_status()
    return response.json()
