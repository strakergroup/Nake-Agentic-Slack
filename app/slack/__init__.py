import os
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.oauth.async_oauth_settings import AsyncOAuthSettings
from .installation_store import AsyncSQLAlchemyInstallationStore
from .state_store import AsyncSQLAlchemyOAuthStateStore
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
installation_store.create_tables()
state_store.oauth_states.create(engine, checkfirst=True)

oauth_settings = AsyncOAuthSettings(
    client_id=os.getenv('SLACK_CLIENT_ID'),
    client_secret=os.getenv('SLACK_CLIENT_SECRET'),
    scopes=[
        'chat:write', 'im:write', 'links:write',
        'channels:history', 'groups:history',
        'im:history', 'mpim:history',
    ],
    installation_store=installation_store,
    state_store=state_store,
    state_validation_enabled=True,
    install_page_rendering_enabled=False,
)


# Initialise Slack app.
app = AsyncApp(
    signing_secret=os.getenv('SLACK_SIGNING_SECRET'),
    oauth_settings=oauth_settings,
)


# Configure Slack events here.
@app.message('hello')
async def message_hello(message, say):
    await say(f'Hey there <@{message["user"]}>!')


slack_handler = AsyncSlackRequestHandler(app)
