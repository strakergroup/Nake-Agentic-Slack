import json
import os
import tempfile
from typing import Any, List

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


def is_ambiguous_api_failure(error: BaseException) -> bool:
    """True when a failed Verify call may still have applied its side effect.

    ``proceed_evaluation_job`` / ``proceed_quality_evaluation`` raise
    :class:`VerifyAPIError` only for definite 401/402/403 rejections. A read
    timeout, a dropped connection, or a 5xx leaves the caller unable to tell
    whether the token debit landed, so the caller must not offer a retry that
    could charge twice.
    """
    if isinstance(error, VerifyAPIError):
        return False
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code >= 500
    return isinstance(error, httpx.TransportError)


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
    *,
    slack_channel_id: str = "",
    pdf_page_count: int | None = None,
    preaccepted_ai_translation_quote: bool = False,
    prequote_message_ts: str | None = None,
    ai_translation_filename_and_languages: list[str] | None = None,
    slack_ht_quote_after_qe: bool = False,
    confirmation_required: bool = True,
):
    target_languages_data: dict[str, Any] = {
        "target_languages": target_languages_uuid,
        "title": reference,
        "source": "slack",
        "workflow_version": workflow_version,
        "docconverter_version": docconverter_version,
        "confirmation_required": confirmation_required,
    }
    if source_language_uuid:
        target_languages_data["sl"] = source_language_uuid
    if job_notes:
        target_languages_data["client_notes"] = job_notes
    if slack_channel_id:
        target_languages_data["slack_channel_id"] = slack_channel_id
    if pdf_page_count is not None and pdf_page_count > 0:
        target_languages_data["pdf_page_count"] = str(pdf_page_count)
    if preaccepted_ai_translation_quote:
        target_languages_data["preaccepted_ai_translation_quote"] = "true"
    if prequote_message_ts:
        target_languages_data["prequote_message_ts"] = prequote_message_ts
    if slack_ht_quote_after_qe:
        target_languages_data["slack_ht_quote_after_qe"] = "true"
    if ai_translation_filename_and_languages:
        target_languages_data["ai_translation_filename_and_languages"] = (
            ai_translation_filename_and_languages
        )
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
    purchase_order_number: str = "",
):
    """
    Create a human job in the Verify API

    Args:
        ray_client: RayClient object
        job_uuid: UUID of the job
        file_and_languages: List of strings with the format "file_uuid:language_uuid"
        purchase_order_number: Client reference to show in LanguageCloud and Job Portal.
    """

    url = f"{domains.verify_api}/automation/service/create-human-job"
    headers = {"Authorization": f"Bearer {ray_client.id_token}"}
    # TODO: allow submission
    service_uuid = "37f2e44b-ba3c-42b1-83c7-d3023298292f"
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
    *,
    assumed_quality_tier: str | None = None,
):
    url = f"{domains.verify_api}/automation/service/pricing"
    headers = {"Authorization": f"Bearer {ray_client.id_token}"}
    # TODO: Update for multiple files
    file_and_languages = [
        f"{file_uuid}:{lang}" for lang in language_uuids for file_uuid in file_uuids
    ]
    data = {"job_uuid": job_uuid, "file_and_languages": file_and_languages}
    if assumed_quality_tier:
        data["assumed_quality_tier"] = assumed_quality_tier
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


async def get_evaluation_job_quote(
    ray_client: RayClient,
    job_uuid: str,
    services: list[str],
    *,
    file_and_languages: list[str] | None = None,
) -> dict:
    """Fetch post-extract token quote for selected evaluate services."""
    query_params: dict[str, str | list[str]] = {"services": services}
    if file_and_languages:
        query_params["file_and_languages"] = file_and_languages
    params = httpx.QueryParams(query_params)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{domains.verify_api}/evaluate/{job_uuid}/quote/credits",
            params=params,
            headers={"Authorization": f"Bearer {ray_client.id_token}"},
        )
        if response.status_code == 401:
            raise VerifyAPIError(
                "Unauthorized: Your authentication token is invalid or expired. Please reconnect your account.",
                401,
            )
        if response.status_code == 403:
            raise VerifyAPIError(
                "Forbidden: You don't have permission to access this resource.", 403
            )
        response.raise_for_status()
        return response.json()


async def proceed_evaluation_job(
    ray_client: RayClient,
    job_uuid: str,
    *,
    token_cost: int,
    skip_quality_evaluation: bool = False,
    ai_translation_file_and_languages: list[str] | None = None,
) -> dict:
    """Proceed with an evaluate job after the user accepts a service quote."""
    data: dict[str, str | list[str]] = {
        "uuid": job_uuid,
        "tokenCost": str(token_cost),
        "source": "slack",
        "skip_quality_evaluation": "true" if skip_quality_evaluation else "false",
    }
    if ai_translation_file_and_languages:
        data["ai_translation_file_and_languages"] = ai_translation_file_and_languages
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{domains.verify_api}/evaluate/proceed",
            data=data,
            headers={"Authorization": f"Bearer {ray_client.id_token}"},
        )
        if response.status_code == 401:
            raise VerifyAPIError(
                "Unauthorized: Your authentication token is invalid or expired. Please reconnect your account.",
                401,
            )
        if response.status_code == 403:
            raise VerifyAPIError(
                "Forbidden: You don't have permission to access this resource.", 403
            )
        if response.status_code == 402:
            raise VerifyAPIError("Insufficient AI token balance.", 402)
        response.raise_for_status()
        return response.json()


async def proceed_quality_evaluation(
    ray_client: RayClient,
    job_uuid: str,
    *,
    token_cost: int,
    human_translation_file_and_languages: list[str] | None = None,
    quality_evaluation_file_and_languages: list[str] | None = None,
) -> dict:
    """Proceed with quality evaluation after AI translation has completed."""
    data: dict[str, Any] = {
        "tokenCost": str(token_cost),
        "source": "slack",
    }
    if human_translation_file_and_languages:
        data["human_translation_file_and_languages"] = (
            human_translation_file_and_languages
        )
    if quality_evaluation_file_and_languages:
        data["quality_evaluation_file_and_languages"] = (
            quality_evaluation_file_and_languages
        )
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{domains.verify_api}/evaluate/{job_uuid}/proceed-quality-evaluation",
            data=data,
            headers={"Authorization": f"Bearer {ray_client.id_token}"},
        )
        if response.status_code == 401:
            raise VerifyAPIError(
                "Unauthorized: Your authentication token is invalid or expired. Please reconnect your account.",
                401,
            )
        if response.status_code == 403:
            raise VerifyAPIError(
                "Forbidden: You don't have permission to access this resource.", 403
            )
        if response.status_code == 402:
            raise VerifyAPIError("Insufficient AI token balance.", 402)
        response.raise_for_status()
        return response.json()
