"""Handlers for Slack workspace lifecycle events."""

from typing import Any, Dict

from app.auth.connector import RayContext, disconnect_ray_super_group_and_users
from app.ray.settings import update_channel_id


async def handle_app_uninstalled(context: RayContext):
    # https://api.slack.com/events/app_uninstalled
    # Disconnect the Super Group and all users linked to the Slack workspace
    # when the app is uninstalled.
    # RAY-59799: This is a requirement of the Slack app directory submission.
    disconnect_ray_super_group_and_users(context["team_id"], context.enterprise_id)


async def handle_channel_id_changed(event: Dict[str, Any]):
    old_channel_id = event.get("old_channel_id")
    new_channel_id = event.get("new_channel_id")

    if old_channel_id and new_channel_id:
        await update_channel_id(old_channel_id, new_channel_id)
