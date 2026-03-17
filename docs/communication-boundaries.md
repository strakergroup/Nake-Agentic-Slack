# Communication Boundaries — Sequence Diagram

This document describes the communication boundaries between the Slack App and all internal/external services it interacts with.

> For a detailed catalogue of every endpoint and Redis stream event, see [Internal Services & Redis Stream Events](internal-services.md).

---

## Participants

| Participant | Description |
|---|---|
| **Slack** | Slack's platform — sends authenticated HTTP events, actions, commands to the app |
| **Slack App** | This service slack-straker-translate (`slack-ray-translator` - previous name) — FastAPI + Slack Bolt |
| **Slack API** | Slack's web API — used by the app to post messages, upload files, etc. |
| **Stream Proxy** | Internal event bus — receives published events and routes them to consumers |
| **Internal Services** | RAY platform, MT services, transcription consumers, cloud-verify-consumer, etc. |
| **redis-slack-consumer** | Internal Redis consumer — listens for Slack-specific events and calls back into this app |

---

## Sequence Diagram

```mermaid
sequenceDiagram
    autonumber

    participant Slack as Slack
    participant App as Slack App
    participant Proxy as Stream Proxy
    participant Services as Internal Services
    participant Redis as redis-slack-consumer

    %% 1. Slack Platform to App
    Slack->>App: POST /slack/* (X-Slack-Signature auth)
    App-->>Slack: HTTP 200 ack + Slack API calls

    %% 2. App to Internal Services
    App->>Proxy: POST /events/mt-service:mt:translate:multi
    App->>Proxy: POST /events/slack:job:machine:translate:v2
    Proxy-->>App: HTTP 200
    Proxy->>Services: route event to consumer

    %% 3. Internal Services direct callback
    Services->>App: POST /ray/callback (X-Straker-Signature auth)
    App-->>Services: HTTP 200
    App->>Slack: notify user via Slack API

    %% 4. redis-slack-consumer to App
    Services->>Proxy: publish result event
    Proxy->>Redis: route to redis-slack-consumer
    Redis->>App: POST /ray/events (RayEvent payload)
    App-->>Redis: HTTP 200
    App->>Slack: post result via Slack API
```

---

## Route Summary

### Inbound from Slack
| Route | Method | Purpose |
|---|---|---|
| `/slack/*` | GET / POST | All Slack platform events, actions, commands, shortcuts — handled by Slack Bolt (`app/routers/slack.py`) |

### Inbound from Internal Services
| Route | Method | Purpose |
|---|---|---|
| `/ray/events` | POST | Event results from `redis-slack-consumer` (transcription, translation, embedding, MT, RAY job events) — handled in `app/routers/ray.py` |
| `/ray/callback` | POST | Direct job status callbacks from the RAY platform — signature validated via `X-Straker-Signature` |

### Outbound to External / Internal
| Destination | Transport | Purpose |
|---|---|---|
| **Slack** | HTTPS (Slack SDK) | Post messages, upload files, open modals, ephemeral messages |
| **Stream Proxy** | HTTP POST | Publish MT translation requests, SRT translation jobs, and other processing events |
| **RAY / Internal APIs** | HTTP | Auth lookups, job pricing, evaluation jobs, credit spending |
