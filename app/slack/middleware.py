"""Middleware for Slack Bolt listeners.

See https://slack.dev/bolt-python/concepts#listener-middleware.
"""

from .auth.connector import get_ray_client, get_app_id
from .templates.messages import LoginMessage


async def load_ray_client(context, body, next) -> None:
    """Gets and saves the DeltaRay client information of the Slack user to
    the context if the accounts are connected. Also add a `login_prompt` dict
    to the context containing the blocks and text to be sent to the user asking
    them to connect their DeltaRay account.
    """
    app_id = body.get(
        "api_app_id", get_app_id(context["bot_token"], context["team_id"])
    )
    context["ray_client"] = get_ray_client(
        context["user_id"],
        context["team_id"],
        app_id,
    )

    message = LoginMessage(
        context["user_id"],
        context["team_id"],
        app_id,
        context.get("channel_id", context["user_id"]),
    )
    # TODO use message class
    context["login_prompt"] = {
        "blocks": message.blocks,
        "text": message.text,
    }
    await next()
