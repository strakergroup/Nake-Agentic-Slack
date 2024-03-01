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
