"""Middleware for Slack Bolt listeners.

See https://slack.dev/bolt-python/concepts#listener-middleware.
"""

from .auth.connector import get_ray_client_id


async def get_client_id(context, body, next):
    """Gets and saves the DeltaRay client id of the Slack user if the
    accounts are connected.
    """
    context['client_id'] = get_ray_client_id(
        context['user_id'],
        context['team_id'],
        body['api_app_id']
    )
    await next()
