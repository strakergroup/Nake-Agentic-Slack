# Arbitr agent

An agent layer for this app: a person states a goal in plain language in a DM, the
Slack agent panel, or an @-mention, and the agent uses the app's existing functions
to get it done. It replaces the IBM Watson Assistant intent matcher for free-text
messages. Shortcuts, the slash command, forms and the Home tab are unchanged.

**Status: tested core, reviewed wiring, not yet run.** The core has 131 automated
tests that run anywhere. The adapter was written from a reading of this codebase and
has never executed, because the app cannot start outside Straker's network. Start
with [the run-book](arbitr-agent-runbook.md).

The experience this code is meant to produce is shown, step by step, in the front-end
POC: https://claude.ai/artifact/Sxx8yDUnwVW6E8bbGVFerL . [arbitr-agent-poc-map.md](arbitr-agent-poc-map.md)
maps each scene to the code behind it.

## One switch

`AGENT_ENABLED` (default `false`). With it off the app behaves exactly as before: the
seam falls straight through to Watson, no listeners are registered, the event hook
returns immediately. It also needs `ANTHROPIC_API_KEY`; without a key the agent stays off.

| Variable | Default | Effect |
|---|---|---|
| `AGENT_ENABLED` | `false` | Master switch. |
| `ANTHROPIC_API_KEY` | unset | Required for the agent to switch on. Read from the environment, not from `integration_keys`. |
| `AGENT_MODEL` | `claude-opus-5` | The model behind the vendor-neutral port. |
| `AGENT_NATIVE_DOCUMENT_QUOTES` | `false` | See "Documents" below. |
| `AGENT_SUGGESTIONS_ENABLED` | `false` | Reserved. The suggestion rules exist and are tested; the channel trigger is not wired. |
| `AGENT_FOLLOWUPS_ENABLED` | `false` | Reserved. See "What is not wired". |

## Two layers

```
app/agent/core/       all agent logic; imports nothing from the rest of the app
app/agent/adapters/   the only code that touches the app, Bolt and Slack
```

`tests/agent/test_core_isolation.py` fails if the core ever imports `app.*` (outside
itself) or a private Straker package. That rule is what lets the core be tested on a
laptop, and it keeps your review small: the adapters and five short edits.

**Core**

| File | What it does |
|---|---|
| `types.py` | `AgentFacts`, `Session`, `PendingApproval`, `ToolSpec`, `ToolOutcome`, `TurnRecord`. |
| `runner.py` | The only orchestrator: `handle_message`, `handle_approval`, `handle_stop`, `handle_backend_event`. |
| `llm.py` | `LlmPort` (vendor-neutral) and `ClaudeLlm`. A manual tool loop, because a turn can pause for hours on a click and must resume from Redis in another process. |
| `prompt.py` | System prompt. Frozen rules first, per-request facts last, so the prompt cache can serve the long part. |
| `tools.py` | The nine tools and their strict schemas. |
| `approvals.py` | The approval gate. |
| `session.py` | JSON sessions in Redis, per-thread lock, duplicate-event guard, stop flag, quote-to-session map. |
| `slack_ui.py` | Pure builders for every Slack payload. |
| `suggestions.py`, `followups.py` | Rules for speaking first. No model involved. |
| `copy.py`, `voice.py` | Every fixed sentence, and the voice rules they are tested against. |
| `audit.py` | One `TurnRecord` per turn. |

**Adapters**

| File | What it does |
|---|---|
| `entry.py` | `agent_enabled_for`, `respond_with_agent` (what the seam calls), shared store and model client. |
| `facts.py` | Builds `AgentFacts` from the Bolt context after `ray_connection`. |
| `straker_tools.py` | Binds each tool to an existing function. The only calls into the app. |
| `slack_port.py` | Slack calls. Agent APIs go through `client.api_call` because slack-sdk 3.41 predates them; falls back to plain messages where they are unavailable. |
| `bolt.py` | Agent listeners: approve, decline, stop, quick actions. |
| `events.py` | `notify_backend_event`, called from `/ray/events`. |
| `scheduler.py` | Cron definitions for follow-ups and digest. Not wired. |

## The five edits to existing files

| File | Edit |
|---|---|
| `app/config.py` | Six settings after `media_configure_admin_only`. |
| `app/slack/listener_actions.py` | In `respond_to_message`, immediately before `watson_message(...)`: if the agent is enabled and handles the message, return. If the agent fails, `respond_with_agent` returns `False` and Watson answers as before. |
| `app/slack/listeners.py` | Registers the agent's listeners above the catch-all `@app.event(re.compile(r".+"))`, only when enabled. Adds `arbitr` to the slash-command pattern. |
| `app/routers/ray.py` | One call at the top of `ray_events`. It never raises. |
| `Pipfile`, `.env.example` | `anthropic`, and the variables above. |

`git diff --stat origin/master -- app/config.py app/slack/listener_actions.py app/slack/listeners.py app/routers/ray.py`
shows the size: under 40 lines.

## What a turn does

1. **Claim the event.** The agent keeps its own duplicate guard keyed on the Slack event.
   The app's `is_duplicate_event` needs an `enterprise_id`, so workspaces outside
   Enterprise Grid have none; without this a Slack retry would run, and bill, a turn twice.
2. **Lock the thread**, load or create the session, append the message.
3. **Facts first.** `facts_from_context` tells the agent who the person is, whether they
   are connected, whether they may see quotes (`user_may_receive_quotes`), whether the
   workspace is IBM (`is_ibm_customer_enterprise`), and their locale. The model is told
   these; it never works them out.
4. **Reason.** Up to eight model steps. Look-up tools run immediately. A gated tool does
   not run: the runner creates a pending approval, shows buttons, and tells the model the
   approval is waiting.
5. **Reply**, save, write one audit record.

Slack attaches buttons only when a streamed message ends, so a request and its execution
are two messages with two plans. "Waiting on you" is the session status (`suspended`),
never a task card: Slack's task cards have no waiting state.

## Money and posting

The agent cannot submit, accept, pay for or cancel anything. This is structural:

- **Quoted work.** The agent never calls a submission or acceptance function. With
  `AGENT_NATIVE_DOCUMENT_QUOTES=true` and a person who may see quotes, it calls
  `enqueue_document_mt_quote_preflight`, which only prices. Your existing quote message,
  your Accept button and your validation do the rest.
- **People who cannot see quotes** are handed the existing Document MT form through a
  button, so their experience, including the one click on Submit, is exactly today's.
- **Posting for others to see** (`post_translation_publicly`) is the one gated tool. It runs
  only from `handle_approval` after `ApprovalGate.verify`: the click must come from the
  person who asked, within 15 minutes, once, on a session that has not been stopped. The
  button carries only a random id; the action that runs is the input stored at proposal
  time. Sabotaging `is_gated` makes seven tests fail.
- **Inline translation is metered, not quoted**, exactly as the translate shortcut is today:
  `require_mt_tokens`, then `get_mt_translation`. The agent says so when it offers a public post.
- `tests/agent/test_adapters_static.py` parses the adapters and fails if they reference
  `enqueue_document_mt_submission`, `document_machine_translate`, `handle_document_mt_submit`,
  `accept_document_mt_quote`, `cancel_document_mt_quote`, `new_job`, `cancel_job`,
  `submit_job`, `create_human_job` or `mark_document_mt_quote_accepted`.

Stop (Slack's native button, `agent_session_stopped`) cancels anything not yet approved.
Work already accepted and paid for carries on; cancelling a paid job stays its own explicit action.

## What the language model sees

It sees the person's request, the facts above, and file metadata (name, type, size). It
never sees document contents, file URLs, or channel thread text: `translate_text` with
`use_current_thread` fetches the thread and sends it to the MT service without returning
it to the model. Text inside a message, file name or tool result is treated as information,
never as an instruction.

Sub-processor impact: Anthropic (or Claude through AWS Bedrock or Google Vertex, since the
port is vendor-neutral) receives conversation text typed to the agent. The Marketplace
security tab currently names gpt-3.5-turbo on Azure OpenAI, yet there is no LLM client
anywhere in this repository. That tab needs correcting whichever way this goes.

Suggestions: the app already receives messages in channels it is a member of; that is how
channel auto-translate works. A suggestion would add one `/mt/detect` call to LanguageCloud
and nothing to the model provider. `suggestions.decide` has no parameter that could carry
message text, and a test enforces that.

## Failure behaviour

| Situation | What the person sees |
|---|---|
| Model unreachable, rate limited, 5xx | Fixed copy plus quick-action buttons that work without the model. |
| Tool raises | The model is told once, with no exception text. A second failure ends the turn with fixed copy. |
| Model refuses | Fixed copy. |
| Eight steps without finishing | Fixed copy. |
| Any unexpected exception in the agent | `notify_exception`, then Watson answers as today. |
| IBM workspace, reply mentions connecting, balances or top-ups | Reply replaced with safe fixed copy; outcome recorded as `partial`. |
| Person who cannot see quotes, reply contains an amount | Reply replaced. |

## Documents: why the native quote is off by default

The Document MT form owns the language list (`language_mt_options`), the same-language-family
rule, the file accessibility check and the duplicate-submission check. `request_document_quote`
mirrors those steps in the same order and reuses your helpers, but it has not been run, and
language codes chosen by a model are validated only against `get_language_options()`. Until
you have reviewed that one function, leave the flag off: everyone gets the form, as today.

## What is not wired

- **Follow-ups and digest.** `followups.py` decides who gets what (one reminder per waiting
  quote, one note per delivery, quiet hours, opt-in digest) and is tested. `scheduler.py`
  defines two SAQ cron jobs, but the data access inside them is not written, and nothing
  registers them. The app has no scheduler today; adding `cron_jobs` to a worker is yours to decide.
- **Channel suggestions.** Rules and blocks are tested. The trigger in the `message` handler,
  per-channel state, and the member-language count are not written.
- **Suggested prompts, feedback buttons, `app_context_changed`, resuming after account connect.**
  Shown in the POC. Suggested prompts need `app_home_opened`, which already has a listener
  here, and Bolt runs only the first matching listener, so it needs an edit to yours.
- **Localisation.** The fixed copy in `core/copy.py` is English only and is not yet passed
  through `_()`. Model-written replies already follow the person's language.
- **Session storage** is Redis with a seven-day TTL, keys under `slack-ray-translator:agent:`.
  A table would be better for audit; database changes are yours.

## Voice

In conversation the agent speaks as "I". Notices, buttons and attributions name Arbitr. It
never says "we", which would blur an automated actor into a team of people. No exclamation
marks, no emoji, no long dashes, never "users". A refusal has three parts: what happened,
why, what happens next. `core/voice.py` checks all of this mechanically and every fixed
string in `core/copy.py` is tested against it. New fixed copy belongs in `copy.py`, and the
adapter should pass it through `_()` when you localise it.
