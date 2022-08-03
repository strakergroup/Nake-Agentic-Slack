import logging
import os
from slack_bolt import BoltResponse
from slack_bolt.async_app import AsyncApp
from slack_bolt.oauth.async_oauth_settings import AsyncOAuthSettings
from slack_bolt.oauth.async_callback_options import (
    DefaultAsyncCallbackOptions,
    AsyncSuccessArgs,
)
from ray_sdk.api.ray import Ray
from .auth.stores import AsyncSQLAlchemyInstallationStore
from .auth.stores import AsyncSQLAlchemyOAuthStateStore
from .templates.messages import OnboardingMessage
from ..database import engine

# Mock API credentials
ray = Ray(
    url="https://local-api.strakertranslations.com",
    client_id="",
    secret="",
    token="Zymq2+7imGHJ7Ee/vrPWpUm40/KcH8F87Kmj9BSOEVdCHa3EmQ8mMZYZP3EbYeSrMAtlNuVvLIO3a1SBC04dvRj6kkyHDMkVdMxbXhJuHThtANNtpMWgFwTpWwaNvu4Xw8OKbEYzCdeW84aCYoAWFA==",
)


installation_store = AsyncSQLAlchemyInstallationStore(
    client_id=os.getenv("SLACK_CLIENT_ID"),
    engine=engine,
    bots_table_name="slack_bots",
    installations_table_name="slack_installations",
)
state_store = AsyncSQLAlchemyOAuthStateStore(
    expiration_seconds=1800,
    engine=engine,
    table_name="slack_oauth_states",
)

# Create the Slack tables if they do not exist.
installation_store.metadata.create_all(engine, checkfirst=True)
state_store.metadata.create_all(engine, checkfirst=True)

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
