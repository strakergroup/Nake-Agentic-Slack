"""This module registers listeners to handle events, interactions,
commands, etc. from the Slack API.
"""
import re
import logging
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from .app import app
from .middleware import get_client_id
from .blocks import onboarding_block, login_block

# logging.basicConfig(level=logging.INFO)


# ---------------------------------------------------------
# Set up Slack events here.
# ---------------------------------------------------------

@app.message(re.compile(r'\bTJ\d+\b', re.IGNORECASE))
async def message_hello(message, context, say):
    job_ids = (id.upper() for id in context['matches'])
    await say(f'Job ({", ".join(job_ids)})')


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
