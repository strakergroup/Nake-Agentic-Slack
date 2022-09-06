from typing import Any, Callable, Coroutine
import inspect
import logging
import functools
from slack_bolt.request.payload_utils import (
    is_event,
    is_block_actions,
    is_slash_command,
    is_options,
    is_global_shortcut,
    is_message_shortcut,
    is_view_submission,
    is_view_closed,
)
from ray_logger.slack import SlackMySQLLogger, SlackAppLog

from ..database import engines


# The singleton logger for logging Slack events and actions.
slack_app_logger = SlackMySQLLogger(
    engines.ray_integration_log,
    slack_log_table="slack_logs",
    watson_log_table="slack_logs_watson",
    api_log_table="slack_logs_api",
)


def init_slack_app_log(body: dict[str, Any], context: dict[str, Any]) -> SlackAppLog:
    """Parses the body and context of a Slack Bolt listener and returns
    a `SlackAppLog` with a valid `SlackLog` to be used for `ray_logger`.

    Returns:
        SlackAppLog: A valid log object to be logged with `ray_logger`.
    """
    # Automatically determine the action type and value.
    action_type = None
    action_value = None
    ts = None
    if is_event(body):
        action_type = "event"
        action_value = body["event"]["type"]
        if "subtype" in body["event"]:
            action_value = f"{action_value}:{body['event']['subtype']}"
        ts = body["event"].get("event_ts")
    elif is_block_actions(body):
        action_type = "block_action"
        action_value = body["actions"][0].get("action_id")
        ts = body["actions"][0].get("action_ts") if body.get("actions") else None
    elif is_slash_command(body):
        action_type = "command"
        action_value = body.get("command")
        if "text" in body:
            action_value = f"{action_value} {body['text']}"
        # ts is not in the command payload.
    elif is_options(body):
        action_type = "options"
        action_value = body.get("action_id")
        # ts is not in the options payload.
    elif is_global_shortcut(body):
        action_type = "global_shortcut"
        action_value = body.get("callback_id")
        # TODO: test this
        ts = body.get("action_ts")
    elif is_message_shortcut(body):
        action_type = "message_shortcut"
        action_value = body.get("callback_id")
        # TODO: test this
        ts = body.get("action_ts")
    elif is_view_submission(body):
        action_type = "view_submission"
        action_value = body["view"].get("callback_id")
        # ts is not in the view_submission payload.
    elif is_view_closed(body):
        action_type = "view_closed"
        action_value = body["view"].get("callback_id")
        # ts is not in the view_closed payload.
    else:
        pass  # The action_type and action_value will remain as None

    return SlackAppLog.from_slack(
        action_type,
        action_value,
        user_id=context.get("user_id"),
        team_id=context.get("team_id"),
        channel_id=context.get("channel_id"),
        ts=ts,
        body=body,
    )


def slack_log_decorator(
    listener_func: Callable[..., Coroutine]
) -> Callable[..., Coroutine]:
    """A decorator for Slack Bolt listener functions to log with `ray_logger`
    at the end of the function.

    Args:
        listener_func: The Slack Bolt listener function.
    """
    listener_sig = inspect.signature(listener_func)
    context_in_listener = "context" in listener_sig.parameters

    # Add the "context" argument to wrapper() if it does not exist in listener_func().
    if context_in_listener:
        wrapper_sig_func = listener_func
    else:
        # This is a placeholder function to set the signature of the listener
        # function the additional "context" argument.
        async def wrapper_sig_func():
            pass

        params = list(listener_sig.parameters.values())
        # Limitation: No positional only arguments allowed in the listener function.
        params.insert(
            0, inspect.Parameter("context", inspect.Parameter.POSITIONAL_OR_KEYWORD)
        )
        wrapper_sig_func.__signature__ = listener_sig.replace(parameters=params)

    @functools.wraps(wrapper_sig_func)
    async def wrapper(context, *args, **kwargs):
        # Put the context back into kwargs if needed.
        if context_in_listener:
            kwargs["context"] = context

        await listener_func(*args, **kwargs)

        # Log with ray_logger at the end of the function.
        if "log" in context and isinstance(context["log"], SlackAppLog):
            # TODO: log async
            slack_app_logger.log(context["log"])
        else:
            logging.warning("The SlackAppLog object ('log') is not in the context")

    return wrapper
