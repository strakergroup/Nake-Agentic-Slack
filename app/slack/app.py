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
from ..database import engines


installation_store = AsyncSQLAlchemyInstallationStore(
    client_id=os.getenv("SLACK_CLIENT_ID"),
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
    client_id=os.getenv("SLACK_CLIENT_ID"),
    client_secret=os.getenv("SLACK_CLIENT_SECRET"),
    scopes=[
        "chat:write",
        "chat:write.public",
        "im:write",
        "links:write",
        "links:read",
        "files:read",
        "im:read",
        "channels:history",
        "groups:history",
        "im:history",
        "mpim:history",
        "commands",
    ],
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
            args.installation.app_id,
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
            extra={"reason": args.reason, "request": args.request.body},
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
