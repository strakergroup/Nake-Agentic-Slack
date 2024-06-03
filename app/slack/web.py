"""Utility functions for using the Slack Web API."""

from typing import Any, Iterable
import os
import asyncio
import json
import tempfile
import httpx
from pathlib import Path
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError
from buglog import notify_exception

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
    files = map_file_options(files)
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
    http: httpx.AsyncClient | None,
) -> str:
    """Downloads a file from Slack and saves it to the disk.

    Args:
        client (WebClient): The Slack WebClient instance (with auth token).
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
        response = await http.get(
            download_url, headers={"Authorization": f"Bearer {client.token}"}
        )
        response.raise_for_status()
    except httpx.HTTPStatusError:
        # 302 status if auth token is invalid.
        raise
    finally:
        # Close the http connection if no httpx client given.
        if not reuse_connection:
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


async def download_files(client: AsyncWebClient, files: Iterable[str]) -> list[str]:
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


async def set_mt_ts_edit(
    client: AsyncWebClient, send_ts: str,  reply_ts: str, count: int = 100,
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
        await redis_conn.set(key, json.dumps(reply_ts), ex=3600)
    except Exception as e:
        notify_exception(e)
    return reply_ts


async def get_mt_ts_cached(send_ts: str) -> str:
    key = f"slack-ray-translator:mt_ts:{send_ts}"
    cached = False
    mt_timestamp = ''
    try:
        cached = await redis_conn.get(key)
    except Exception as e:
        notify_exception(e)
    if cached:
        try:
            mt_timestamp = json.loads(cached)
            assert isinstance(mt_timestamp, dict)
            return mt_timestamp
        except Exception as e:
            notify_exception(e)

    return mt_timestamp
