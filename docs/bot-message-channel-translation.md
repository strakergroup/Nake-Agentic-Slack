# Bot Message Channel Translation

Slack bot messages can be translated by the existing channel translation pipeline when channel translation is enabled for the channel.

## Flow

```mermaid
sequenceDiagram
    participant Slack
    participant Listener as message_event
    participant Redis
    participant MT as MT translation stream

    Slack->>Listener: message.bot_message
    Listener->>Listener: Ignore DMs and this app's own posts
    Listener->>Listener: Load channel translation settings
    Listener->>Listener: Detect source language and target languages
    Listener->>Redis: Consume bot/channel rolling quota
    alt quota available
        Listener->>MT: Send channel_translation request
    else quota exhausted
        Listener-->>Slack: No translation request
    end
```

## Rate Limit

Bot channel translations are limited to 10 bot messages per bot, per channel, per 5-minute window. The Redis key is scoped by Slack channel ID and Slack `bot_id`.

The quota is consumed once per bot message, regardless of how many channel translation languages are configured. If Redis cannot record the quota, bot translation fails closed and no MT request is sent.
