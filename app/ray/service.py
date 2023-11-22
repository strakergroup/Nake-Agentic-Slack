import asyncio
from typing import Any, Callable, Coroutine, Iterable, TypeVar
from functools import wraps
from urllib.parse import urlencode
from buglog import notify_exception
import httpx
from httpx import Response
from ray_sdk import RayV3, RayResponse, RayAuthError, RayAPIResponseError
from ray_sdk.api.v3.models import (
    Job,
    JobSummary,
    Language,
    Pagination,
    Quote,
    GroupOptions,
)

from ..config import config, domains, Environment
from ..auth.connector import RayClient, encrpyt_slack_integration_token


F = TypeVar("F", bound=Callable[..., Coroutine])


def secured_endpoint(func: F) -> F:
    """Decorator that raises an `AssertionError` before the function is
    called if this RayService is not authenticated (has token + client_id).
    """

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        if not self.has_credentials():
            raise AssertionError(
                f"The RayService does not have credentials for: {func.__name__}"
            )
        return await func(self, *args, **kwargs)

    return wrapper


class RayService:
    """A wrapper for the RAY sdk. The services can be cached by the
    RAY client ID and token.
    """

    # Cache of RayServices. The key is a tuple of ray_client_id and token.
    services: dict[tuple[str, str], "RayService"] = {}

    def __init__(self, ray_client_id: str | None, token: str | None, lc_token: str | None) -> None:
        self._ray_client_id = ray_client_id
        self._ray = RayV3(api_token=token, base_url=domains.stingray,
                          lc_api_id_token=lc_token, lc_base_url=domains.languagecloud_api)

    @property
    def ray_client_id(self) -> str | None:
        return self._ray_client_id

    @property
    def token(self) -> str | None:
        return self._ray.api_token

    @property
    def lc_token(self) -> str | None:
        return self._ray.lc_api_id_token

    def has_credentials(self) -> bool:
        """Returns `True` if this service has a RAY client ID and access token.
        Used before making an authenticated request to check if it would fail
        beforehand.
        """
        return bool(self.ray_client_id and self.token)

    async def get_languages(self) -> RayResponse[list[Language]]:
        """Gets the list of available languages for translation."""
        return await self._ray.get_languages()

    @secured_endpoint
    async def get_job(
        self,
        job_id: str,
        page: int = 1,
        page_size: int = 5,
    ) -> tuple[Job | None, Response | None]:
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

    @secured_endpoint
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

    @secured_endpoint
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

    @secured_endpoint
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
        translation_notes: str | None = None,
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
            config.base_url, urlencode({"client_id": self.ray_client_id})
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
                    translation_notes=translation_notes,
                    additional_data={"app_source": "slack"},
                )
            )

        result = await asyncio.gather(*tasks)
        asyncio.create_task(self._ray.api_ondemand_process("slack"))

        return result

    @secured_endpoint
    async def get_quote(self, job_id: str) -> tuple[Quote | None, Response | None]:
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

    @secured_endpoint
    async def get_groups(self) -> list[GroupOptions]:
        """Gets the list of groups."""
        response = await self._ray.get_groups()
        return response.data

    @secured_endpoint
    async def get_machine_translation(
        self,
        target_lang: str | None = None,
        source_lang: str | None = None,
        sentence: str | None = None,
    ) -> RayResponse[dict[str, str]]:
        """Gets the machine translation from the goolge api by correct target and source langauge."""
        response = await self._ray.get_machine_translation(
            target_lang=target_lang,
            source_lang=source_lang,
            sentence=sentence,
        )

        return response.data, response.response

    @classmethod
    def get_service(
        cls, ray_client: RayClient | str, token: str | None = None
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
        if not ray_client_id or not token:
            raise ValueError(
                "Either a RayClient or a RAY client ID and token combination"
                "must be given"
            )

        lc_token = encrpyt_slack_integration_token(
            user_id=ray_client.slack_user_id, team_id=ray_client.slack_team_id, enterprise_id=ray_client.slack_enterprise_id, channel_id=ray_client.slack_team_id)

        key = (ray_client_id, token, lc_token)
        if key not in cls.services:
            cls.services[key] = cls(ray_client_id=ray_client_id,
                                    token=token, lc_token=lc_token)
        return cls.services[key]


# These functions are for RAY endpoints that do not require authentication.


_noauth_service = RayService(None, None, None)


async def get_languages() -> RayResponse[list[Language]]:
    return await _noauth_service.get_languages()


async def get_job_predictions(job_ids: list[str]) -> list[dict[str, Any]]:
    """Gets the job on-time predictions from the ml-job-on-time-prediction API.

    Args:
        job_ids (list[str]): The list of job IDs to check.

    Returns:
        list[bool]: A list of dictionaries containing the job ID and on time status.
    """
    job_predictions = [
        {"job_id": job_id.upper(), "prediction": ""} for job_id in job_ids
    ]
    if config.environment == Environment.production or config.environment == Environment.local:
        # Disable predictions on live for now.
        return job_predictions
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{domains.job_on_time_prediction}/predict",
                json={"job_ids": [job_id.upper() for job_id in job_ids]},
            )
            predictions = r.json()
            if predictions:
                return predictions
    except Exception as e:
        notify_exception(e)
        return job_predictions
    return job_predictions
