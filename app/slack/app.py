import logging
import os
from slack_bolt import BoltResponse
from slack_bolt.async_app import AsyncApp
from slack_bolt.oauth.async_oauth_settings import AsyncOAuthSettings
from slack_bolt.oauth.async_callback_options import (
    DefaultAsyncCallbackOptions,
    AsyncSuccessArgs,
    AsyncFailureArgs,
)
from buglog import notify_exception

from .stores import AsyncSQLAlchemyInstallationStore, AsyncSQLAlchemyOAuthStateStore
from .templates.messages import OnboardingMessage
from ..config import config
from ..database import engines


installation_store = AsyncSQLAlchemyInstallationStore(
    client_id=config.slack_client_id,
    engine=engines["ray_integration"],
    bots_table_name="slack_bots",
    installations_table_name="slack_installations",
)
state_store = AsyncSQLAlchemyOAuthStateStore(
    expiration_seconds=3600,
    engine=engines["ray_integration"],
    table_name="slack_oauth_states",
)

oauth_settings = AsyncOAuthSettings(
    client_id=config.slack_client_id,
    client_secret=config.slack_client_secret.get_secret_value(),
    scopes=[
        "app_mentions:read",
        "channels:history",
        "chat:write",
        "chat:write.public",
        "commands",
        "files:read",
        "groups:history",
        "im:history",
        "mpim:history",
        # "users:read",
        # "users:read.email",
        # "links:write",
        # "links:read",
    ],
    # Request user token individually rather than during installation.
    # So keep this empty.
    # user_scopes=[
    #     "chat:write",
    # ],
    installation_store=installation_store,
    state_store=state_store,
    state_validation_enabled=True,
    install_page_rendering_enabled=False,
)


class RayCallbackOptions(DefaultAsyncCallbackOptions):
    """This class overrides the default callback handler to send an onboarding
    message to the user who installed the app.
    """

    async def _success_handler(self, args: AsyncSuccessArgs) -> BoltResponse:
        # Send onboarding message to the user who installed the app.
        app.client.token = args.installation.bot_token
        message = OnboardingMessage(
            args.installation.user_id,
            args.installation.team_id,
            args.installation.enterprise_id,
            args.installation.user_id,
        )
        await app.client.chat_postMessage(
            channel=args.installation.user_id, blocks=message.blocks, text=message.text
        )
        return await super()._success_handler(args)

    async def _failure_handler(self, args: AsyncFailureArgs) -> BoltResponse:
        notify_exception(
            args.error,
            msg="Slack App failed to install",
            extra={
                "reason": args.reason,
                "suggested_status_code": args.suggested_status_code,
                "request": args.request.body,
            },
        )
        if args.reason == "invalid_browser":
            # The user installed from the wrong starting URL, e.g. Slack app directory.
            # Should be /slack/install. This is because it requires a state token
            # generated from this app.
            # Redirect to the correct URL.
            return BoltResponse(status=307, headers={"Location": "/slack/install"})
        elif args.reason == "access_denied" or args.suggested_status_code == 200:
            # If the user cancelled the installation, redirect to the public Landing Page.
            # TODO: Delete state token
            return BoltResponse(
                status=307,
                headers={
                    "Location": "https://www.strakertranslations.com/products/ray-translate-app-for-slack"  # noqa: B950
                },
            )
        return await super()._failure_handler(args)


oauth_settings.callback_options = RayCallbackOptions(
    logger=logging.getLogger(__name__),
    state_utils=oauth_settings.state_utils,
    redirect_uri_page_renderer=oauth_settings.redirect_uri_page_renderer,
)


# Initialise the Slack app.
app = AsyncApp(
    signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
    oauth_settings=oauth_settings,
)
