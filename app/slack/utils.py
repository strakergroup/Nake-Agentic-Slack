import logging
import re
import traceback
from typing import Any, Callable

from slack_sdk.models.blocks import (
    ActionsBlock,
    ButtonElement,
    HeaderBlock,
    MarkdownTextObject,
    PlainTextObject,
    SectionBlock,
)
from slack_sdk.web.async_client import AsyncWebClient

from app.config import Environment, config, domains
from app.translate import _

logger = logging.getLogger(__name__)


def is_channel_im(channel_id: str | None) -> bool:
    """Check if a channel is an IM (direct message between the bot and a user)
    by looking at the channel ID. There may be false negatives, e.g. if the
    channel ID begins with "C". Also check that the channel_type is "im".

    Args:
        channel_id (str | None): The Slack channel ID.

    Returns:
        bool: The channel is an IM.
    """
    if not channel_id:
        return False
    return (
        channel_id.startswith("D")
        or channel_id.startswith("U")
        or channel_id.startswith("W")
    )


def format_strings_display(strings: list[str], *, and_string: str = "&") -> str:
    """Format a list of strings for display in human-readable form.

    E.g. English, French and Spanish.

    Args:
        strings (list[str]): The list of strings to format.
        and_string (str, optional): The "and" string to use. Defaults to "&".

    Returns:
        str: The formatted string.
    """
    and_string = _(and_string)
    if not strings:
        return ""
    if len(strings) == 1:
        return strings[0]
    if len(strings) == 2:
        return f"{strings[0]} {and_string} {strings[1]}"
    return f"{', '.join(strings[:-1])}, {and_string} {strings[-1]}"


def strip_slack_formatting(text: str) -> str:
    """Strip Slack formatting from a string. Slack uses it's own special form
    of Markdown to format text. This function will remove some of the formatting,
    but is not perfect.

    See:
        https://api.slack.com/reference/surfaces/formatting
        https://api.slack.com/reference/surfaces/formatting#retrieving-messages

    Args:
        text (str): The original Slack text.

    Returns:
        str: The text with formatting stripped.
    """
    # TODO Unit test
    if not text:
        return ""
    # Remove user and channel mentions.
    text = re.sub(r"<(#C|@U|@W|!).*?>", "", text)
    # Links
    text = unformat_links(text)
    # Blockquote
    # text = re.sub(r"(^|\n)(>|&gt;)\s?", "\n", text).lstrip()
    # More if needed...
    return text.strip()


def unformat_links(text: str) -> str:
    """Convert links in a Slack text to plain text. Some text may be impossible
    to parse if it uses special characters, e.g. <, >, |.

    See:
        https://api.slack.com/reference/surfaces/formatting#retrieving-messages

    Args:
        text (str): The original Slack text.

    Returns:
        str: The text with links unformatted.
    """
    if not text:
        return ""
    # TODO Unit test
    # Slack returns weirdly formatted text if the link text contains <, >, |.
    # <a (link) -> &lt;<https://example.com>|&lt;a&gt;
    # a> (link) -> <http://example.com|a>&gt;
    # <a> (link) -> &lt;<https://example.com>|&lt;a&gt;&gt;
    # a<b>c (link) -> &lt;<https://example.com>|a&lt;b&gt;c&gt; (cannot parse)

    # <https://example.com> -> https://example.com
    text = re.sub(r"<(http[^|>]+)>", r"\1", text)
    # &lt;https://example.com|abc&gt; -> abc
    # &lt;https://example.com|ab|cd&gt; -> ab|cd
    # https://stackoverflow.com/questions/406230/regular-expression-to-match-a-line-that-doesnt-contain-a-word
    text = re.sub(r"&lt;(?:(?!&gt;)[^|])+\|(.+?)&gt;", r"\1", text)
    # <https://example.com|abc> -> abc
    # <https://example.com|ab|cd> > ab|cd
    text = re.sub(r"<[^|>]+\|(.+?)>", r"\1", text)
    # Convert &lt; and &gt; to < and >
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return text


def escape_slack_emoji(text: str):
    """Escape Slack emoji characters in text.

    Args:
        text (str): The text to escape.

    Returns:
        str: The text with Slack emoji characters escaped.
    """
    # Slack uses :emoji: syntax for emoji. If the text contains :emoji:,
    # to prevent translation replace with <x i={i}> where i is the source index.
    emojis = re.findall(r":[^\s]*?:|<[^\s]*>", text)
    for i, emoji in enumerate(emojis):
        text = text.replace(emoji, f"<x i={i}/>", 1)
    return text


def unescape_slack_emoji(translated_text: str, source_text: str) -> str:
    """Unescape Slack emoji characters in text.

    Args:
        translated_text (str): The text to unescape form google translate.

    Returns:
        str: The text with Slack emoji characters escaped.
    """
    # Slack uses :emoji: syntax for emoji. If the text contains :emoji:,
    # place back the emojis from the source text. Based on the i index value of the x tag
    # Find all :emoji: in the source
    emojis = re.findall(r":[^\s]*?:|<[^\s]*>", source_text)
    # ensure translated text has spacing removed
    translated_text = re.sub(
        r"<\s*x\s*i\s*=\s*(\d*)\s*/\s*>", replace_xtag, translated_text
    )
    # Replace <x i={i}> with the original :emoji: from the source text
    for i, emoji in enumerate(emojis):
        translated_text = translated_text.replace(f"<x i={i}/>", emoji, 1)
    return translated_text


def replace_xtag(match):
    i = match.group(1)
    return f"<x i={i}/>"


def segment_quality_score(score: float, taus_version: str = "1.0.0") -> str:
    """
    Determine the quality score based on the TAUS QE version.

    Args:
        score (float): The quality score.
        taus_version (str): The TAUS QE version. Defaults to "1.0.0".

    Returns:
        str: The quality category ("best", "good", "acceptable", "bad").
    """
    if taus_version == "2.0.0":
        # TAUS QE version 2.0.0
        if score >= 0.9:
            return _("Overall Translation Quality: Best")
        elif score >= 0.88:
            return _("Overall Translation Quality: Good")
        elif score >= 0.8:
            return _("Overall Translation Quality: Acceptable")
        return _("Overall Translation Quality: Bad")
    else:
        # TAUS QE version 1.0.0
        if score >= 0.95:
            return _("Overall Translation Quality: Best")
        elif score >= 0.9:
            return _("Overall Translation Quality: Good")
        elif score >= 0.85:
            return _("Overall Translation Quality: Acceptable")
        return _("Overall Translation Quality: Bad")


def _wrap_notify_exception(
    original_notify_exception: Callable[..., bool],
) -> Callable[..., bool]:
    """Wrap buglog.notify_exception to also send to Slack.

    This creates a wrapper function that:
    1. Calls the original buglog.notify_exception (sends to BugLogHQ)
    2. Also sends to Slack dev alert channel in the background

    Returns a function with the same signature as buglog.notify_exception.
    """

    def wrapped_notify_exception(
        exc: BaseException | None = None,
        msg: str | None = None,
        extra: dict[str, Any] | None = None,
        severity: str = "ERROR",
    ) -> bool:
        """Wrapper that sends to both BugLogHQ and Slack."""
        # Call original to send to BugLogHQ
        result = original_notify_exception(exc, msg, extra, severity)

        # Also send to Slack (async, fire-and-forget) if we have an exception
        if exc is not None:
            try:
                import asyncio

                # Try to get the current event loop
                try:
                    loop = asyncio.get_running_loop()
                    # If we're in an async context, schedule the task
                    loop.create_task(_send_to_slack_background(exc))
                except RuntimeError:
                    # No running loop, create one for this task
                    # Use a thread to avoid blocking
                    import threading

                    def run_async():
                        asyncio.run(_send_to_slack_background(exc))

                    thread = threading.Thread(target=run_async, daemon=True)
                    thread.start()
            except Exception:
                # Don't let Slack failures break BugLogHQ notifications
                logger.debug("Failed to schedule Slack notification", exc_info=True)

        return result

    return wrapped_notify_exception


async def _send_to_slack_background(exc: BaseException) -> None:
    """Background function to send exception to Slack without context."""
    try:
        token = config.slack_dev_alert_bot_token.get_secret_value()
        channel_id = config.slack_dev_alert_channel_id

        if not channel_id or not token:
            return

        # Format exception information
        error_type = type(exc).__name__
        error_message = str(exc)
        error_traceback = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )

        # Truncate traceback if too long
        max_traceback_length = 3000
        if len(error_traceback) > max_traceback_length:
            error_traceback = (
                error_traceback[:max_traceback_length] + "\n... (truncated)"
            )

        # Build message
        env_prefix = (
            f"{config.environment.title()}"
            if config.environment != Environment.production
            else ""
        )

        # Create BugLogHQ link
        buglog_url = f"{domains.buglog}/bugLog/"

        # Fallback text for notifications
        fallback_text = f"🚨 Exception: {error_type} - {error_message}"

        # Build Block Kit blocks
        blocks = [
            HeaderBlock(
                text=PlainTextObject(
                    text="🚨 Exception (from BugLogHQ)",
                    emoji=True,
                )
            ),
            SectionBlock(
                fields=[
                    MarkdownTextObject(
                        text=f"*Environment:*\n{env_prefix or 'Production'}"
                    ),
                    MarkdownTextObject(text=f"*Error Type:*\n`{error_type}`"),
                ]
            ),
            SectionBlock(
                text=MarkdownTextObject(text=f"*Error Message:*\n{error_message}")
            ),
            SectionBlock(
                text=MarkdownTextObject(
                    text=f"*Traceback:*\n```\n{error_traceback}\n```"
                )
            ),
            ActionsBlock(
                elements=[
                    ButtonElement(
                        text=PlainTextObject(text="View in BugLogHQ", emoji=True),
                        url=buglog_url,
                        action_id="view_buglog",
                        style="primary",
                    )
                ]
            ),
        ]

        # Create Slack client and send message
        slack_client = AsyncWebClient(token=token)
        await slack_client.chat_postMessage(
            channel=channel_id,
            text=fallback_text,
            blocks=blocks,
        )

        logger.info(
            f"Successfully sent exception notification to Slack channel {channel_id}"
        )

    except Exception as slack_error:
        # Don't let Slack notification failures break the app
        logger.error(
            f"Failed to send exception to Slack: {slack_error}. "
            f"Original exception: {type(exc).__name__}: {exc}"
        )
