import os
from fastapi import APIRouter, Request
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.oauth.async_oauth_settings import AsyncOAuthSettings
from slack_sdk.oauth.installation_store.sqlalchemy import SQLAlchemyInstallationStore
from slack_sdk.oauth.installation_store import FileInstallationStore
from slack_sdk.oauth.state_store import FileOAuthStateStore
from ..database import engine


installation_store = SQLAlchemyInstallationStore(
    client_id=os.getenv('SLACK_CLIENT_ID'),
    engine=engine,
    bots_table_name='slack_bots',
    installations_table_name='slack_installations',
)

# Create the Slack tables if they do not exist.
installation_store.installations.create(engine, checkfirst=True)
installation_store.bots.create(engine, checkfirst=True)

oauth_settings = AsyncOAuthSettings(
    client_id=os.getenv('SLACK_CLIENT_ID'),
    client_secret=os.getenv('SLACK_CLIENT_SECRET'),
    scopes=[
        'chat:write', 'im:write', 'links:write',
        'channels:history', 'groups:history',
        'im:history', 'mpim:history',
    ],
    # TODO Use DB store
    installation_store=FileInstallationStore(base_dir='./data/installations'),
    state_store=FileOAuthStateStore(expiration_seconds=1800, base_dir='./data/states'),
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


handler = AsyncSlackRequestHandler(app)


# Connect the Slack Bolt endpoints to FastAPI
router = APIRouter(tags=['slack'])


@router.api_route('/slack/{path:path}', methods=['GET', 'POST'])
async def slack(request: Request):
    return await handler.handle(request)
