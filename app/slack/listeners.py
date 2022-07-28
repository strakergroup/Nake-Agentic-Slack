"""This module registers listeners to handle events, interactions,
commands, etc. from the Slack API.
"""
import re
import logging
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from .app import app
from .middleware import load_ray_client
from .templates.messages import OnboardingMessage, HelpMessage, WhoamiMessage
from .templates.modals import new_job_modal

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
        message = OnboardingMessage(event.get('user'), body['team_id'], body['api_app_id'], event.get('channel'))
        await say(
            blocks=message.blocks,
            text=message.text
        )


@app.global_shortcut('new_job', middleware=[load_ray_client])
async def new_job(ack, shortcut, context, client):
    await ack()
    if context['ray_client']:
        await new_job_modal(client, shortcut['trigger_id'], context['ray_client']['username'])
    else:
        # Prompt login if accounts are not connected yet.
        await client.chat_postEphemeral(
            channel=context['user_id'],
            user=context['user_id'],
            blocks=context['login_prompt']['blocks'],
            text=context['login_prompt']['text']
        )


@app.command('/ray', middleware=[load_ray_client])
async def ray_command(ack, say, respond, command, context, client):
    await ack()
    if context['ray_client']:
        match command.get('text', '').split(' '):
            case ['whoami']:
                await respond(WhoamiMessage(context["ray_client"]["username"]).text)
            case ['login' | 'signin' | 'connect']:
                await respond(
                    blocks=context['login_prompt']['blocks'],
                    text=context['login_prompt']['text']
                )
            case ['logout' | 'signoff']:
                await respond('Logout prompt')
            case ['new']:
                await new_job_modal(client, command['trigger_id'], context['ray_client']['username'])
            case [command_text]:
                match = re.fullmatch('TJ\d+', command_text, re.IGNORECASE)
                if match:
                    await say(f'Job info: {command_text}')
                else:
                    await respond(blocks=HelpMessage().blocks, text=HelpMessage().text)
            case _:
                await respond(blocks=HelpMessage().blocks, text=HelpMessage().text)
    else:
        # Prompt login if accounts are not connected yet.
        await respond(
            blocks=context['login_prompt']['blocks'],
            text=context['login_prompt']['text']
        )


@app.action('login')
async def login(ack):
    # No need to do anything here, user opened a link.
    await ack()


@app.action('link')
async def login(ack):
    """Simple link button action. No additional actions required."""
    await ack()


# FastAPI will use this to handle Slack API requests.
slack_handler = AsyncSlackRequestHandler(app)
