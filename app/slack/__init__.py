import os
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.oauth.async_oauth_settings import AsyncOAuthSettings
from .installation_store import AsyncSQLAlchemyInstallationStore
from .state_store import AsyncSQLAlchemyOAuthStateStore
from .blocks import onboarding_block
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
        'chat:write', 'im:write', 'links:write',
        'channels:history', 'groups:history',
        'im:history', 'mpim:history', 'links:read',
        'commands',
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

@app.event('app_home_opened')
async def home_opened(client, event, body, say):
    # Send an onboarding message if the app home is opened for the first time.
    # TODO also onboard if the user hasn't opened in a long time and the account is not connected
    history = await client.conversations_history(channel=event.get('channel'), limit=1)
    if not history.get('messages'):
        await say(
            blocks=onboarding_block(event.get('user'), body.get('team_id'), body.get('api_app_id')),
            text='The Straker RAY App has been sucessfully installed in your Slack workspace! :tada:'
        )

@app.command('/ray')
async def ray_command(ack, say, command):
    await ack()
    await say('Ray command')
    # command['channel_id']
    # command['user_id']
    # command['team_id']

@app.action('login')
async def login(body, ack, say, logger):
    # No need to do anything here, user opened a link.
    await ack()


slack_handler = AsyncSlackRequestHandler(app)
