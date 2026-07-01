# Bot Message Channel Translation

Slack bot messages can be translated by the existing channel translation pipeline when channel translation is enabled for the channel.

## Flow

```mermaid
sequenceDiagram
    participant Slack
    participant Listener as message_event
    participant Redis
    participant MT as MT translation stream

    Slack->>Listener: message (bot_id set)
    Listener->>Listener: Skip placeholder-only messages
    Listener->>Listener: Load channel translation settings
    Listener->>Listener: Detect source language and target languages
    Listener->>Redis: Bump edit generation + consume bot quota (creates only)
    alt quota available
        Listener->>MT: Send channel_translation request
    else quota exhausted
        Listener-->>Slack: No translation request
    end
    MT->>Listener: slack:direct:mt:result
    Listener->>Listener: Discard stale generation / post or update translation
```

## Guards (RAY-80512)

- **Placeholder-only messages** — text with no translatable content is not translated. This covers Slack `:emoji:` tokens (for example `:3dotsloading:`), bare ellipsis (`...` / `…`), other punctuation, and Unicode emoji. Detection strips `:emoji:` tokens then skips the message when no Unicode letter or digit remains (`is_untranslatable_placeholder`); anything with a real letter/digit still translates.
- **Separate posts translate separately** — each distinct bot message in the channel gets its own translation (subject to rate limit and placeholder skip). Coalescing applies only to **edits** of the same message (`message_changed`), not to a burst of new posts.
- **Edit generation** — each translate request bumps a Redis generation counter keyed by source `message_ts`. MT callbacks with an older generation are discarded so only the latest edit wins.
- **Edit rate limit exemption** — `message_changed` re-translations do not consume the bot rolling quota.

## Rate Limit

Bot channel translations are limited to 10 **new** bot messages per bot, per channel, per 5-minute window. The Redis key is scoped by Slack channel ID and Slack `bot_id`.

Edits (`message_changed`) are excluded from this quota. The quota is consumed once per new bot message, regardless of how many channel translation languages are configured. If Redis cannot record the quota, bot translation fails closed and no MT request is sent.

## Translation reply cache (`mt_ts`)

When a translation is first posted, the bot reply timestamp is written synchronously to Redis (`slack-ray-translator:mt_ts:{source_ts}`) so a subsequent edit can `chat.update` the same reply. Edit-generation discard handles out-of-order MT completions; the synchronous cache write keeps the edit path reliable without waiting on a background worker.

## Reporting Metadata

Bot channel translations carry `is_bot=true` in the MT callback metadata and the `/mt/inline-usage` reporting payload. The Slack poster id uses the bot user id from the message when Slack provides it, while the bot display name from `bot_profile.name` is used as the reporting `client_name` fallback if Slack profile lookup cannot return a human-style profile.
