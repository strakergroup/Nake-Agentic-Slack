import asyncio
from typing import Any, Iterable
from urllib.parse import urlencode

from httpx import Response
from ray_sdk import RayAPIResponseError, RayAuthError, RayResponse, RayV3
from ray_sdk.api.v3.models import (
    Job,
    JobSummary,
    Pagination,
)

from app.slack.buglog_notifier import notify_exception

from ..auth.connector import RayClient
from ..config import domains


class RayService:
    """A wrapper for the RAY sdk. The services can be cached by the
    RAY client ID and token.
    """

    # Cache of RayServices. The key is a tuple of ray_client_id and token.
    # Limit cache size to prevent memory issues (LRU eviction when limit is reached)
    _max_cache_size = 100
    services: dict[tuple[str, str, str], "RayService"] = {}

    def __init__(
        self, ray_client_id: str | None, token: str | None, id_token: str | None
    ) -> None:
        self._ray_client_id = ray_client_id
        self._ray = RayV3(
            api_token=token,
            lc_id_token=id_token,
            base_url=domains.stingray,
            lc_base_url=domains.languagecloud_api,
        )

    @property
    def ray_client_id(self) -> str | None:
        return self._ray_client_id

    @property
    def token(self) -> str | None:
        return self._ray.api_token

    @property
    def lc_token(self) -> str | None:
        return self._ray.lc_id_token

    def has_credentials(self) -> bool:
        """Returns `True` if this service has a RAY client ID and access token.
        Used before making an authenticated request to check if it would fail
        beforehand.
        """
        return bool(self.ray_client_id and self.token)

    async def get_languages(self):
        """Gets the list of available languages for translation."""
        return await self._ray.get_languages()

    async def get_job(
        self,
        job_id: str,
        page: int = 1,
        page_size: int = 5,
    ) -> tuple[list[Job] | None, Response | None]:
        """Gets the details of a translation job.

        Args:
            job_id (str): The reference/ID of the job.

        Returns:
            The job data and the response if they exist.
        """
        try:
            response = await self._ray.get_job(job_id, page, page_size)
            return response.data, response.response
        except RayAuthError as e:
            return None, e.response
        except RayAPIResponseError as e:
            return None, e.response
        except Exception as e:
            notify_exception(e)
            return None, None

    async def get_job_summary(
        self,
        statuses: list[str],
        from_hours: int = 0,
        started_from: int = 0,
        completed_from: int = 0,
        quoted_from: int = 0,
        due_before: int = 0,
    ) -> RayResponse[JobSummary]:
        """Gets the client's job summary."""
        return await self._ray.get_job_summary(
            status=statuses,
            from_hours=from_hours,
            started_from=started_from,
            completed_from=completed_from,
            quoted_from=quoted_from,
            due_before=due_before,
        )

    async def get_job_list(
        self,
        status: str | None = None,
        client_ref: str | None = None,
        from_hours: int = 0,
        started_from: int = 0,
        completed_from: int = 0,
        quoted_from: int = 0,
        due_before: int = 0,
        page: int = 1,
        page_size: int = 5,
    ) -> RayResponse[tuple[list[Job], Pagination]]:
        """Gets the client's list of jobs filtered."""
        return await self._ray.get_job_list(
            status=status,
            client_ref=client_ref,
            from_hours=from_hours,
            started_from=started_from,
            completed_from=completed_from,
            quoted_from=quoted_from,
            due_before=due_before,
            page=page,
            page_size=page_size,
        )

    async def new_job(
        self,
        files: Iterable[str],
        sl: str,
        tl: list[str],
        workflow: str,
        timeframe: str = "5",
        group_id: str | None = None,
        reference: str | None = None,
        job_notes: str | None = None,
        # translation_notes: str | None = None,
    ) -> list[RayResponse[None]]:
        """Submit a new job.

        Args:
            files (Iterable[str]): A list of paths of files to submit.
            sl (str): The source language code.
            tl (list[str]): A list of target language codes.
            workflow (str): The API workflow.
            timeframe (str): The API priority. Defaults to 5.
            reference (str | None, optional): A job reference. Defaults to None.
            job_notes (str | None, optional): The job notes. Defaults to None.
            translation_notes (str | None, optional): The translation notes. Defaults to None.

        Returns:
            list[RayResponse[None]]: The responses of the API requests made.
        """
        callback_uri = "{}/ray/callback?{}".format(
            domains.slack_ray_translator, urlencode({"client_id": self.ray_client_id})
        )
        tasks = []
        for path in [p for p in files if p]:
            tasks.append(
                self._ray.new_job(
                    file_path=path,
                    title="Slack job",
                    sl=sl,
                    tl=tl,
                    group_id=group_id,
                    reference=reference,
                    workflow=workflow,
                    timeframe=timeframe,
                    callback_uri=callback_uri,
                    job_notes=job_notes,
                    # translation_notes=translation_notes,
                    additional_data={"app_source": "slack"},
                )
            )

        result = await asyncio.gather(*tasks)
        asyncio.create_task(self._ray.api_ondemand_process("slack"))

        return result

    async def get_quote(self, job_id: str):
        """Gets the quote for the job.

        Args:
            job_id (str): The reference/ID of the job.

        Returns:
            The quote data and the response if they exist.
        """
        try:
            response = await self._ray.get_quote(job_id)
            return response.data, response.response
        except RayAuthError as e:
            return None, e.response
        except RayAPIResponseError as e:
            return None, e.response

    async def get_groups(self):
        """Gets the list of groups."""
        response = await self._ray.get_groups()
        return response.data

    async def cancel_job(
        self,
        job_id: str = "",
        job_uuid: str = "",
    ) -> tuple[dict[str, Any] | None, Response | None]:
        """Gets the details of a translation job.

        Args:
            job_id (str): The reference/ID of the job.

        Returns:
            The job data and the response if they exist.
        """
        try:
            response = await self._ray.cancel_job(job_id, job_uuid)
            return response.data, response.response
        except RayAuthError as e:
            return None, e.response
        except RayAPIResponseError as e:
            return None, e.response

    @classmethod
    def get_service(
        cls,
        ray_client: RayClient | str,
        token: str | None = None,
        id_token: str | None = None,
    ) -> "RayService":
        """Gets the RayService instance for a particular RAY client. Instances
        created with this method are cached. Can pass either a RayClient
        instance or a combination of the RAY client id and access token.

        Args:
            ray_client (str | RayClient): A RayClient instance or a RAY client ID.
            token (str | None, optional): The access token connected with the RAY
                client. Ignored if a RayClient is given. Required if a RAY client
                ID is given.

        Raises:
            ValueError: The RAY client ID was given but not the token.
        """
        ray_client_id = (
            ray_client.id if isinstance(ray_client, RayClient) else ray_client
        )
        token = ray_client.access_token if isinstance(ray_client, RayClient) else token
        id_token = (
            ray_client.id_token if isinstance(ray_client, RayClient) else id_token
        )
        if not ray_client_id or not token or not id_token:
            raise ValueError(
                "Either a RayClient or a RAY client ID and token combination"
                "must be given"
            )
        key = (ray_client_id, token, id_token)
        if key not in cls.services:
            # Evict oldest entries if cache is full (simple FIFO eviction)
            if len(cls.services) >= cls._max_cache_size:
                # Remove the first (oldest) entry
                oldest_key = next(iter(cls.services))
                del cls.services[oldest_key]
            cls.services[key] = cls(
                ray_client_id=ray_client_id, token=token, id_token=id_token
            )
        return cls.services[key]


# These functions are for RAY endpoints that do not require authentication

_noauth_service = RayService(None, None, None)


async def get_languages():
    return await _noauth_service.get_languages()
