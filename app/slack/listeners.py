import os
import re
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.oauth.async_oauth_settings import AsyncOAuthSettings
from .installation_store import AsyncSQLAlchemyInstallationStore
from .state_store import AsyncSQLAlchemyOAuthStateStore
from .middleware import get_client_id
from .blocks import onboarding_block, login_block
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


# ---------------------------------------------------------
# Set up Slack events here.
# ---------------------------------------------------------

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
            blocks=onboarding_block(event.get('user'), body.get('team_id'), body.get('api_app_id'), event.get('channel')),
            text='The Straker RAY App has been sucessfully installed in your Slack workspace! :tada:'
        )


@app.command('/ray', middleware=[get_client_id])
async def ray_command(ack, say, respond, command, context):
    await ack()
    # Check if connected to DeltaRay account.
    if context['client_id']:
        match command.get('text', '').split(' '):
            case ['whoami']:
                await respond(f'Client ID: {context["client_id"]}')
            case ['login' | 'signin' | 'connect']:
                # login prompt
                await respond(
                    blocks=login_block(command['user_id'], command['team_id'], command['api_app_id'], command['channel_id']),
                    text='Connect your DeltaRay account'
                )
            case ['logout' | 'signoff']:
                await respond('Logout prompt')
            case [command_text]:
                match = re.fullmatch('TJ\d+', command_text, re.IGNORECASE)
                if match:
                    await say(f'Job info: {command_text}')
                else:
                    await respond('Show help')
            case _:
                await respond('Show help')
    else:
        # login prompt
        await respond(
            blocks=login_block(command['user_id'], command['team_id'], command['api_app_id'], command['channel_id']),
            text='Connect your DeltaRay account'
        )


@app.action('login')
async def login(ack):
    # No need to do anything here, user opened a link.
    await ack()


# FastAPI will use this to handle Slack API requests.
slack_handler = AsyncSlackRequestHandler(app)
