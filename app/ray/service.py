import asyncio
from urllib.parse import urlencode
from slack_sdk.web.async_client import AsyncWebClient
from ray_sdk import RayV3, RayResponse, RayAuthError, RayAPIResponseError
from ray_sdk.api.v3.models import Job, Language

from ..config import config
from ..auth.connector import RayClient
from ..slack import web
from ..slack.templates.models import NewJobForm


class RayService:
    """A wrapper for the RAY sdk. The services can be cached by the
    RAY client ID and token.
    """

    # Cache of RayServices. The key is a tuple of ray_client_id and token.
    services: dict[tuple[str, str], "RayService"] = {}

    def __init__(self, ray_client_id: str | None, token: str | None) -> None:
        self._ray_client_id = ray_client_id
        self._ray = RayV3(api_token=token, base_url=config.stingray_domain)

    @property
    def ray_client_id(self) -> str | None:
        return self._ray_client_id

    @property
    def token(self) -> str | None:
        return self._ray.api_token

    def has_credentials(self) -> bool:
        """Returns `True` if this service has a RAY client ID and access token.
        Used before making an authenticated request to check if it would fail
        beforehand.
        """
        return bool(self.ray_client_id and self.token)

    async def get_languages(self) -> RayResponse[list[Language]]:
        """Gets the list of available languages for translation."""
        return await self._ray.get_languages()

    async def get_job(self, job_id: str) -> RayResponse[Job] | None:
        """Gets the details of a translation job.

        Args:
            job_id (str): The reference/ID of the job.

        Returns:
            Job | None: The job details, or `None` if access denied.
        """
        if not self.token:
            return None
        try:
            return await self._ray.get_job(job_id)
        except RayAuthError:
            return None
        except RayAPIResponseError:
            # TODO: log API errors
            return None

    async def submit_job(
        self, client: AsyncWebClient, form: NewJobForm
    ) -> list[RayResponse[None]]:
        """Submit a new job."""
        if not self.has_credentials():
            raise ValueError("The RayService does not have credentials for: submit_job")
        file_ids = (file.id for file in form.files if file.id)
        # TODO: check if file is downloaded
        file_paths = await web.download_files(client, file_ids)
        callback_uri = "{}/ray/callback?{}".format(
            config.base_url, urlencode({"client_id": self.ray_client_id})
        )
        tasks = []
        for path in [p for p in file_paths if p]:
            tasks.append(
                self._ray.new_job(
                    file_path=path,
                    title=form.reference or "Slack job",
                    sl=form.source_lang.code,
                    tl=[lang.code for lang in form.target_langs],
                    workflow=form.workflow,
                    callback_uri=callback_uri,
                    additional_data={"app_source": "slack"},
                )
            )
        return await asyncio.gather(*tasks)

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

        key = (ray_client_id, token)
        if key not in cls.services:
            cls.services[key] = cls(ray_client_id=ray_client_id, token=token)
        return cls.services[key]


# These functions are for RAY endpoints that do not require authentication.

_noauth_service = RayService(None, None)


async def get_languages() -> RayResponse[list[Language]]:
    return await _noauth_service.get_languages()
