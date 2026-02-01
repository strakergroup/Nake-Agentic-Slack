"""Utility functions for using the Slack Web API."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

import httpx
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse

from app.constants import FILE_TRANSFER_TIMEOUT
from app.slack.buglog_notifier import notify_exception

from ..redis import redis_conn
from .select_options import map_file_options


async def files_list_simple(
    client: AsyncWebClient, channel_id: str, count: int = 100
) -> list[dict[str, Any]]:
    """A helper method to get the downloadable files accessible by the bot.
    This is a simpler version of `client.files_list()` function.

    Args:
        client (AsyncWebClient): The Slack WebClient instance (with auth token).
        channel_id (str | None, optional): The channel to filter by. Defaults to None.
        count (int, optional): The max number of files to get. Defaults to 100.

    Returns:
        list[dict[str, Any]]: _description_
    """
    key = f"slack-ray-translator:files:{channel_id}"
    response = await client.files_list(
        channel=channel_id,
        count=count,
        show_files_hidden_by_limit=False,
    )
    # Cache files for 1 hour.
    files: list[dict[str, Any]] = response.get("files", [])
    files, _ = map_file_options(files)
    try:
        await redis_conn.set(key, json.dumps(files), ex=3600)
    except Exception as e:
        notify_exception(e)
    return files


async def get_file_info(
    client: AsyncWebClient, files: Iterable[str]
) -> list[dict[str, Any] | BaseException]:
    """Gets the file info of files using the Slack Web API. Multiple files
    are fetched concurrently.

    Args:
        client (AsyncWebClient): The Slack WebClient instance (with auth token).
        files (Iterable[str]): A list of file IDs of the files to get.

    Returns:
        list[dict[str, Any], BaseException]: The list of file objects in the order
        of the file IDs. An element is a BaseException if it raised an exception
        for that file.
    """
    if not files:
        return []
    tasks = (client.files_info(file=file_id) for file_id in files)
    responses = await asyncio.gather(*tasks, return_exceptions=True)
    return [r["file"] if not isinstance(r, BaseException) else r for r in responses]


async def get_bot_accessible_files(
    client: AsyncWebClient, files: Iterable[str]
) -> list[dict[str, Any]]:
    """Gets the file info of the files which are accessible by the Slack app bot.
    This is similar to `get_file_info()`, but it does not include the file info
    of inaccessible files (i.e. files not shared to the bot, e.g. files in DMs or
    private channels). This means that the return list length may be less than the
    number of file IDs given in the argument.

    Args:
        client (AsyncWebClient): The Slack WebClient instance (with auth token).
        files (Iterable[str]): A list of file IDs of the files to get.

    Returns:
        list[dict[str, Any]]: The list of file objects in the order of the file
        IDs EXCLUDING files which the Web API request failed, e.g. due to no access.
    """
    file_info = await get_file_info(client, files)
    return [file for file in file_info if not isinstance(file, BaseException)]


async def download_file(
    client: AsyncWebClient,
    file_id: str,
    *,
    http: httpx.AsyncClient | None = None,
) -> str:
    """Downloads a file from Slack and saves it to the disk.

    Args:
        client (AsyncWebClient): The Slack WebClient instance (with auth token).
        file_id (str): The file ID.
        http (httpx.AsyncClient | None): The httpx client to use. If not given, this
        will create one.

    Returns:
        str: The path of the downloaded file (it is in the temp directory).
    """
    # Download the file from slack.
    try:
        file = await client.files_info(file=file_id)
        download_url = file["file"]["url_private"]
    except SlackApiError:
        # Slack auth error, file_not_found error, etc.
        raise

    reuse_connection = http is not None and not http.is_closed
    if not reuse_connection:
        http = httpx.AsyncClient()
    try:
        if http:
            async with http.stream(
                "GET",
                download_url,
                headers={"Authorization": f"Bearer {client.token}"},
                follow_redirects=True,
                timeout=FILE_TRANSFER_TIMEOUT,
            ) as response:
                response.raise_for_status()

                # Save the file to the temp directory.
                temp_directory = os.path.join(
                    tempfile.gettempdir(), "slack-ray-translator", file_id
                )
                # Create the directory if it doesn't exist.
                Path(temp_directory).mkdir(parents=True, exist_ok=True)
                file_path = os.path.join(temp_directory, file["file"]["title"])

                with open(file_path, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        f.write(chunk)
    except httpx.HTTPStatusError:
        # 302 status if auth token is invalid.
        raise
    finally:
        # Close the http connection if no httpx client given.
        if not reuse_connection and http is not None:
            await http.aclose()

    return file_path


async def download_files(client: AsyncWebClient, files: Iterable[str]):
    """Download multiple files from slack. This is more efficient than calling
    `download_file()` multiple times.

    Args:
        client (WebClient): The Slack WebClient instance (with auth token).
        files (Iterable[str]): A list of file IDs of the files to get.

    Returns:
        list[str | None]: A list of the paths of the downloaded files, an element is
        `None` if the particular file download failed.
    """
    async with httpx.AsyncClient() as http:
        tasks = (download_file(client, file_id, http=http) for file_id in files)
        file_paths = await asyncio.gather(*tasks, return_exceptions=True)
    # Log exceptions.
    for exc in [result for result in file_paths if isinstance(result, Exception)]:
        notify_exception(exc)
    # Return successful file download paths.
    return [result for result in file_paths if isinstance(result, str)]


def _get_mimetype_for_file(filename: str) -> str:
    """Get appropriate mimetype for a file based on extension.

    Args:
        filename: The filename to determine mimetype for

    Returns:
        The mimetype string (defaults to application/octet-stream)
    """
    ext = os.path.splitext(filename)[1].lower()
    mimetype_map = {
        ".srt": "application/x-subrip",
        ".vtt": "text/vtt",
        ".txt": "text/plain",
        ".json": "application/json",
        ".xml": "application/xml",
        ".xlf": "application/xml",
        ".xliff": "application/xml",
        ".csv": "text/csv",
        ".html": "text/html",
        ".htm": "text/html",
    }
    return mimetype_map.get(ext, "application/octet-stream")


async def upload_file_to_slack_memory_efficient(
    client: AsyncWebClient,
    file_path: str,
    channel_id: str,
    title: str | None = None,
    filename: str | None = None,
    initial_comment: str | None = None,
    thread_ts: str | None = None,
) -> AsyncSlackResponse:
    """
    Upload a file to Slack using the external upload flow.

    Uses files_getUploadURLExternal + files_completeUploadExternal which
    preserves the original filename/extension for downloads.

    Args:
        client (AsyncWebClient): The Slack WebClient instance
        file_path (str): Path to the file to upload
        channel_id (str): Channel ID to upload to
        title (str, optional): Title for the file
        filename (str, optional): Filename for the file
        initial_comment (str, optional): Initial comment with the file
        thread_ts (str, optional): Thread timestamp to reply to

    Returns:
        AsyncSlackResponse: Response from Slack API containing file information

    Raises:
        SlackApiError: If the upload fails
        Exception: If any other error occurs during upload
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    # Use provided filename or extract from path
    if not filename:
        filename = os.path.basename(file_path)

    file_size = os.path.getsize(file_path)
    mimetype = _get_mimetype_for_file(filename)

    # Step 1: Get upload URL from Slack
    try:
        upload_response = await client.files_getUploadURLExternal(
            filename=filename,
            length=file_size,
        )

        if not upload_response.get("ok"):
            raise SlackApiError("Failed to get upload URL", upload_response)

        upload_url = upload_response["upload_url"]
        file_id = upload_response["file_id"]

    except SlackApiError as e:
        notify_exception(e, f"Failed to get upload URL for {filename}")
        raise

    # Step 2: Upload file to the provided URL using streaming to avoid OOM for large files
    try:
        # Create an async generator to stream file chunks
        # This prevents OOM errors for large video files (e.g., 800MB+)
        async def stream_file_chunks():
            chunk_size = 64 * 1024  # 64KB chunks
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

        async with httpx.AsyncClient() as http_client:
            http_response = await http_client.post(
                upload_url,
                content=stream_file_chunks(),
                headers={
                    "Content-Type": mimetype,
                    "Content-Length": str(file_size),  # Use pre-calculated size
                },
                timeout=FILE_TRANSFER_TIMEOUT,
            )

            if http_response.status_code != 200:
                raise Exception(
                    f"Upload failed with status {http_response.status_code}: {http_response.text}"
                )

    except Exception as e:
        notify_exception(e, f"Failed to upload file {filename} to Slack")
        raise

    # Step 3: Complete the upload
    try:
        complete_response = await client.files_completeUploadExternal(
            files=[{"id": file_id, "title": title or filename}],
            channel_id=channel_id,
            initial_comment=initial_comment,
            thread_ts=thread_ts,
        )

        if not complete_response.get("ok"):
            raise SlackApiError("Failed to complete upload", complete_response)

        return complete_response

    except SlackApiError as e:
        notify_exception(e, f"Failed to complete upload for {filename}")
        raise


async def set_mt_ts_edit(
    send_ts: str,
    reply_ts: str,
):
    """A helper method to get the thread_ts from the bot message.

    Args:
        client (AsyncWebClient): The Slack WebClient instance (with auth token).
        channel_id (str | None, optional): The channel to filter by. Defaults to None.
        count (int, optional): The max number of thread to get. Defaults to 20.
        thread_ts_dict (dict): The dictionary of thread_ts id for mt send and get message.
    Returns:
        list[dict[str, Any]]: _description_
    """
    key = f"slack-ray-translator:mt_ts:{send_ts}"
    try:
        await redis_conn.set(key, reply_ts, ex=3600)
    except Exception as e:
        notify_exception(e)
    return reply_ts


async def get_mt_ts_cached(send_ts: str):
    key = f"slack-ray-translator:mt_ts:{send_ts}"
    cached = ""
    mt_timestamp = ""
    try:
        cached = await redis_conn.get(key)
    except Exception as e:
        notify_exception(e)
    if cached:
        return cached
    return mt_timestamp
