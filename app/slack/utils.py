import re
from app.translate import _


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
    emojis = re.findall(r":[^\s]*?:", text)
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
    emojis = re.findall(r":[^\s]*?:", source_text)

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
