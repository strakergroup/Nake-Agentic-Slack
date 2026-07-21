"""Handlers for the Home tab help actions."""

from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import RayContext
from app.slack.listener_actions import ai_translate_help, verify_help
from app.slack.middleware import require_ray_client
from app.slack.templates.messages import HumanJobMessage, LoginMessage


async def handle_ai_translate_help(context: RayContext, client: AsyncWebClient):
    """Get ai translate help link. Triggered from the Home AI Translate help button"""
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        await ai_translate_help(client, context, context["ray"].client)


async def handle_verify_help(context: RayContext, client: AsyncWebClient):
    """Get verify help link. Triggered from the Home Verify help button"""
    if await require_ray_client(context, variation=LoginMessage.QUALITY_EVALUATION):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        await verify_help(client, context, context["ray"].client)


async def handle_human_help(context: RayContext, client: AsyncWebClient):
    """Get human translation help. Triggered from the Home Human help button"""
    if await require_ray_client(context, variation=LoginMessage.HUMAN_TRANSLATION):
        msg = HumanJobMessage()
        if context.response_url and context.respond:
            await context.respond(
                text=msg.text,
                blocks=msg.blocks,
                replace_original=False,
            )
        else:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=msg.text,
                blocks=msg.blocks,
            )
