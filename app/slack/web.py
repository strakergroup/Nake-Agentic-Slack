"""Utility functions for using the Slack Web API."""

from typing import Sequence
import os
from pathlib import Path
import tempfile
import asyncio
import httpx
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError


async def download_file(
    client: AsyncWebClient,
    file_id: str,
    *,
    http: httpx.AsyncClient | None,
) -> str:
    """Downloads a file from Slack and saves it to the disk.

    Args:
        client (WebClient): The Slack WebClient instance (with bot token).
        file_id (str): The file ID.
        destination (str, optional): The directory to save the file in. Defaults to ".".
        http (httpx.AsyncClient | None): The httpx client to use. If not given, this
        will create one.

    Returns:
        str: The path of the downloaded file (it is in the temp directory).
    """
    # Download the file from slack.
    try:
        # TODO http errors
        file = await client.files_info(file=file_id)
        download_url = file["file"]["url_private"]
    except SlackApiError:
        # Slack auth error, file_not_found error, etc.
        raise

    close_connection = http is None
    if http is None:
        http = httpx.AsyncClient()
    try:
        response = await http.get(
            download_url, headers={"Authorization": f"Bearer {client.token}"}
        )
        response.raise_for_status()
    except httpx.HTTPStatusError:
        # 302 status if auth token is invalid.
        raise
    finally:
        # Close the http connection if no httpx client given.
        if close_connection:
            await http.aclose()

    # Save the file to the temp directory.
    temp_directory = os.path.join(
        tempfile.gettempdir(), "slack-ray-translator", file_id
    )
    # Create the directory if it doesn't exist.
    Path(temp_directory).mkdir(parents=True, exist_ok=True)
    file_path = os.path.join(temp_directory, file["file"]["title"])
    with open(file_path, "wb") as f:
        f.write(await response.aread())

    return file_path


async def download_files(
    client: AsyncWebClient, files: Sequence[str]
) -> list[str | None]:
    """Download multiple files from slack. This is more efficient than calling
    `download_file()` multiple times.

    Returns:
        list[str | None]: A list of the paths of the downloaded files, an element is
        `None` if the particular file download failed.
    """
    async with httpx.AsyncClient() as http:
        tasks = (download_file(client, file_id, http=http) for file_id in files)
        file_paths = await asyncio.gather(*tasks, return_exceptions=True)
    # TODO: log exceptions?
    # Set failed results as None.
    file_paths = [result if isinstance(result, str) else None for result in file_paths]
    return file_paths
