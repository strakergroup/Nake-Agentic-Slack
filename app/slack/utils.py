import re


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


def strip_formatting(text: str) -> str:
    """Strip Slack formatting from a string. Slack uses it's own special form
    of Markdown to format text. This function will remove some of the formatting,
    but is not perfect.

    See:
        https://api.slack.com/reference/surfaces/formatting

    Args:
        text (str): The original Slack text.

    Returns:
        str: The text with formatting stripped.
    """
    # TODO Unit test
    if not text:
        return ""
    # Blockquote
    text = re.sub(r"(^|\n)(>|&gt;)\s?", "\n", text).lstrip()
    # Links
    text = unformat_links(text)
    # More if needed...
    return text


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
