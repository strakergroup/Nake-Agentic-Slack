import asyncio
import functools
import inspect
import logging
import resource
import sys
import time
from typing import Any, Callable, Coroutine

from ray_logger.slack import SlackAppLog, SlackMySQLLogger
from slack_bolt.request.payload_utils import (
    is_block_actions,
    is_event,
    is_global_shortcut,
    is_message_shortcut,
    is_options,
    is_slash_command,
    is_view_closed,
    is_view_submission,
)

from app.auth.connector import RayContext
from app.slack.buglog_notifier import notify_exception

from ..database import engines

# The singleton logger for logging Slack events and actions.
slack_app_logger = SlackMySQLLogger(
    engines["ray_integration_log"],
    slack_log_table="slack_logs",
    watson_log_table="slack_logs_watson",
    api_log_table="slack_logs_api",
)


def get_memory_mb() -> float:
    """Return the current RSS memory usage of the running process in megabytes.

    On macOS (darwin) ru_maxrss is reported in bytes, whereas on Linux it is
    reported in kilobytes. This helper normalises both to megabytes (MB).
    """
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Convert to MB
    if sys.platform == "darwin":
        return usage / (1024 * 1024)
    return usage / 1024


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
        ts = body.get("action_ts")
    elif is_message_shortcut(body):
        action_type = "message_shortcut"
        action_value = body.get("callback_id")
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

    # Truncate action_value to prevent database errors if it exceeds column size limit
    # The database column has a size limit, so we truncate to 50 characters to be safe
    if action_value is not None and len(action_value) > 50:
        action_value = action_value[:50]

    return SlackAppLog.from_slack(
        action_type,
        action_value,
        user_id=context.get("user_id"),
        team_id=context.get("team_id"),
        channel_id=context.get("channel_id"),
        ts=ts,
        body=body,
    )


async def log_slack(log: SlackAppLog):
    """Wrapper around `log_async()` which logs exceptions to BugLogHQ."""
    try:
        slack_app_logger.log(log)
    except Exception as e:
        notify_exception(e)


def slack_log_decorator(
    listener_func: Callable[..., Coroutine],
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
        wrapper_sig_func.__signature__ = listener_sig.replace(parameters=params)  # type: ignore

    @functools.wraps(wrapper_sig_func)
    async def wrapper(context, *args, **kwargs):
        start_time = time.time()
        mem_start = get_memory_mb()
        # Put the context back into kwargs if needed.
        if context_in_listener:
            kwargs["context"] = RayContext(context)

        await listener_func(*args, **kwargs)
        end_time = time.time()
        duration = end_time - start_time

        mem_end = get_memory_mb()
        mem_delta = mem_end - mem_start

        # Log with ray_logger at the end of the function.
        if "log" in context and isinstance(context["log"], SlackAppLog):
            asyncio.create_task(log_slack(context["log"]))
        else:
            logging.warning("The SlackAppLog object ('log') is not in the context")

        if duration > 5 or mem_delta > 50:
            ts = ""
            if "log" in context and isinstance(context["log"], SlackAppLog):
                ts = context["log"].slack_log.ts
            logging.error(
                f"Slack request performance issue Function {listener_func.__name__} {ts} took {duration:.2f} seconds | Memory +{mem_delta:.2f} MB (total {mem_end:.2f} MB)"
            )

    return wrapper
