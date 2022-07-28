import logging
import os
from slack_bolt import BoltResponse
from slack_bolt.async_app import AsyncApp
from slack_bolt.oauth.async_oauth_settings import AsyncOAuthSettings
from slack_bolt.oauth.async_callback_options import DefaultAsyncCallbackOptions, AsyncSuccessArgs
from .installation_store import AsyncSQLAlchemyInstallationStore
from .state_store import AsyncSQLAlchemyOAuthStateStore
from .templates.blocks import onboarding_block
from ..database import engine


installation_store = AsyncSQLAlchemyInstallationStore(
    client_id=os.getenv('SLACK_CLIENT_ID'),
    engine=engine,
    bots_table_name='slack_bots',
    installations_table_name='slack_installations',
)
state_store = AsyncSQLAlchemyOAuthStateStore(
    expiration_seconds=1800,
    engine=engine,
    table_name='slack_oauth_states',
)

# Create the Slack tables if they do not exist.
installation_store.metadata.create_all(engine, checkfirst=True)
state_store.metadata.create_all(engine, checkfirst=True)

oauth_settings = AsyncOAuthSettings(
    client_id=os.getenv('SLACK_CLIENT_ID'),
    client_secret=os.getenv('SLACK_CLIENT_SECRET'),
    scopes=[
        'chat:write',
        'chat:write.public',
        'im:write',
        'links:write',
        'links:read',
        'channels:history',
        'groups:history',
        'im:history',
        'mpim:history',
        'commands',
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
        await app.client.chat_postMessage(
            channel=args.installation.user_id,
            blocks=onboarding_block(
                args.installation.user_id,
                args.installation.team_id,
                args.installation.app_id,
                args.installation.user_id,
            ),
            text='The Straker RAY App has been sucessfully installed in your Slack workspace! :tada:'
        )
        return await super()._success_handler(args)


oauth_settings.callback_options = RayCallbackOptions(
    logger=logging.getLogger(__name__),
    state_utils=oauth_settings.state_utils,
    redirect_uri_page_renderer=oauth_settings.redirect_uri_page_renderer
)


# Initialise the Slack app.
app = AsyncApp(
    signing_secret=os.getenv('SLACK_SIGNING_SECRET'),
    oauth_settings=oauth_settings,
)
