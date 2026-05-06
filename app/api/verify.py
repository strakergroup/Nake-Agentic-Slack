import json
import os
import tempfile
from typing import List

import httpx

from app.auth.connector import RayClient, SlackUser, get_ray_client
from app.config import config, domains
from app.constants import file_transfer_timeout_for_size
from app.ray.utils import get_filename_from_header
from app.slack.buglog_notifier import notify_exception

from ..redis import redis_conn


class VerifyAPIError(Exception):
    """Custom exception for Verify API errors"""

    def __init__(self, message: str, status_code: int | None = None):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


async def submit_evaluation_job(
    user: RayClient,
    file_path: list[str],
    target_languages_uuid: List[str],
    reference: str,
    source_language_uuid: str = "",
    workflow_uuid: str | None = None,
    job_notes: str = "",
    workflow_version: float = 3.0,
    docconverter_version: str = "m48",
):
    target_languages_data = {
        "target_languages": target_languages_uuid,
        "title": reference,
        "source": "slack",
        "workflow_version": workflow_version,
        "docconverter_version": docconverter_version,
        "confirmation_required": False,
    }
    if source_language_uuid:
        target_languages_data["sl"] = source_language_uuid
    if job_notes:
        target_languages_data["client_notes"] = job_notes
    target_languages_data["workflow"] = workflow_uuid or ""

    max_file_size = max((os.path.getsize(file) for file in file_path), default=None)
    timeout = file_transfer_timeout_for_size(
        max_file_size,
        small_file_threshold_bytes=config.saq_large_file_submission_threshold_mb
        * 1024
        * 1024,
    )

    # Create a job using streaming for file uploads
    async with httpx.AsyncClient(timeout=timeout) as client:
        # Create a multipart form with streaming files
        files = [("files", open(file, "rb")) for file in file_path]

        try:
            response = await client.post(
                f"{domains.verify_api}/evaluate/create",
                files=files,
                data=target_languages_data,
                headers={"Authorization": f"Bearer {user.id_token}"},
            )

            # Check for unauthorized error
            if response.status_code == 401:
                raise VerifyAPIError(
                    "Unauthorized: Your authentication token is invalid or expired. Please reconnect your account.",
                    401,
                )
            elif response.status_code == 403:
                raise VerifyAPIError(
                    "Forbidden: You don't have permission to access this resource.", 403
                )

            response.raise_for_status()
            return response.json()
        finally:
            # Ensure all file handles are closed
            for _, file_obj in files:
                file_obj.close()


async def get_evaluation_job(user: SlackUser, job_uuid: str):
    ray_client = await get_ray_client(user.user_id, user.team_id, user.enterprise_id)
    assert ray_client is not None
    assert ray_client.id_token is not None
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{domains.verify_api}/evaluate/{job_uuid}/files",
            headers={"Authorization": f"Bearer {ray_client.id_token}"},
        )

        # Check for unauthorized error
        if response.status_code == 401:
            raise VerifyAPIError(
                "Unauthorized: Your authentication token is invalid or expired. Please reconnect your account.",
                401,
            )
        elif response.status_code == 403:
            raise VerifyAPIError(
                "Forbidden: You don't have permission to access this resource.", 403
            )

    response.raise_for_status()
    return response.json()


async def get_client_evaluation_job(ray_client: RayClient, job_uuid: str):
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(  # Added missing await
            f"{domains.verify_api}/evaluate/{job_uuid}/files",
            headers={"Authorization": f"Bearer {ray_client.id_token}"},
        )

        # Check for unauthorized error
        if response.status_code == 401:
            raise VerifyAPIError(
                "Unauthorized: Your authentication token is invalid or expired. Please reconnect your account.",
                401,
            )
        elif response.status_code == 403:
            raise VerifyAPIError(
                "Forbidden: You don't have permission to access this resource.", 403
            )

    response.raise_for_status()
    return response.json()


async def download_verify_file(ray_client: RayClient, file_uuid: str):
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(  # Added missing await
            f"{domains.verify_api}/files/{file_uuid}",
            headers={"Authorization": f"Bearer {ray_client.id_token}"},
        )

        # Check for unauthorized error
        if response.status_code == 401:
            raise VerifyAPIError(
                "Unauthorized: Your authentication token is invalid or expired. Please reconnect your account.",
                401,
            )
        elif response.status_code == 403:
            raise VerifyAPIError(
                "Forbidden: You don't have permission to access this resource.", 403
            )

    response.raise_for_status()
    content_disposition = response.headers.get("Content-Disposition")

    # Parse the header to get the filename
    filename = get_filename_from_header(content_disposition)

    # Create a temporary file to avoid loading entire file into memory
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}")
    try:
        # Write the content to temp file in chunks
        async for chunk in response.aiter_bytes():
            temp_file.write(chunk)
        temp_file.close()

        # Return file path instead of BytesIO to avoid memory issues
        file_result = {
            "file_name": filename,
            "file": temp_file.name,  # Return file path instead of BytesIO
        }
        return file_result
    except Exception:
        # Clean up temp file on error
        if os.path.exists(temp_file.name):
            os.unlink(temp_file.name)
        raise


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

        # Check for unauthorized error
        if response.status_code == 401:
            raise VerifyAPIError(
                "Unauthorized: Your authentication token is invalid or expired. Please reconnect your account.",
                401,
            )
        elif response.status_code == 403:
            raise VerifyAPIError(
                "Forbidden: You don't have permission to access this resource.", 403
            )

    response.raise_for_status()
    return response.json()


async def _get_cached_verify_languages(cache_key: str, endpoint_path: str):
    cached = ""
    try:
        cached = await redis_conn.get(cache_key)
    except Exception as e:
        notify_exception(e)
    if cached:
        try:
            languages = json.loads(cached)
            assert isinstance(languages, list)
            return languages
        except Exception as e:
            notify_exception(e)

    url = f"{domains.verify_api}{endpoint_path}"
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.get(url)

        # Check for unauthorized error
        if response.status_code == 401:
            raise VerifyAPIError(
                "Unauthorized: Your authentication token is invalid or expired. Please reconnect your account.",
                401,
            )
        elif response.status_code == 403:
            raise VerifyAPIError(
                "Forbidden: You don't have permission to access this resource.", 403
            )

    response.raise_for_status()
    languages = response.json()["data"]

    languages = [
        {"code": lang["code"], "name": lang["name"], "uuid": lang["uuid"]}
        for lang in languages
    ]
    # Cache languages for 1 hour.
    try:
        await redis_conn.set(cache_key, json.dumps(languages), ex=3600)
    except Exception as e:
        notify_exception(e)
    return languages


async def get_verify_languages():
    return await _get_cached_verify_languages(
        "slack-ray-translator:verify:languages", "/languages"
    )


async def get_verify_source_languages():
    return await _get_cached_verify_languages(
        "slack-ray-translator:verify:source-languages", "/languages/source"
    )


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

        # Check for unauthorized error
        if response.status_code == 401:
            raise VerifyAPIError(
                "Unauthorized: Your authentication token is invalid or expired. Please reconnect your account.",
                401,
            )
        elif response.status_code == 403:
            raise VerifyAPIError(
                "Forbidden: You don't have permission to access this resource.", 403
            )

    response.raise_for_status()
    return response.json()
