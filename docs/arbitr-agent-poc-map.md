# From the POC to the code

POC: https://claude.ai/artifact/Sxx8yDUnwVW6E8bbGVFerL (scripted; nothing in it is connected to Slack).
"Built" means code exists with tests. "Yours" means existing behaviour, reused. "Not built" is shown
in the POC as the target experience and is listed in arbitr-agent.md under "What is not wired".

| POC scene | Status | Where |
|---|---|---|
| **1. Document request** | | |
| Suggested prompts at session start | Not built | Needs `app_home_opened`, which already has a listener here. |
| Agent asks which slide instead of guessing | Built | `core/prompt.py` privacy rule; the model never receives file contents (`runner._user_content`). |
| "Actually, add French too" | Built | Multi-turn sessions, `core/session.py`. |
| Quote plan, then quote, buttons with the quote | Built behind a flag | `request_document_quote` in `adapters/straker_tools.py`, `AGENT_NATIVE_DOCUMENT_QUOTES`. The quote message and its buttons are yours. Default: a button to your form. |
| IBM switch on: one confirming click, no price | Built behind the same flag | `submit_document_translation` (gated), `core/approvals.py`, `runner._gated_presentation`. |
| Typed "yes, go ahead" points back to the button, never a second one | Built | `core/prompt.py`; evals `adversarial-approved-yesterday`, `adversarial-skip-approval`. |
| Accept button, validation, submission | Yours | `app/slack/document_mt_quote_actions.py`. The agent never calls it. |
| Session returns to Ready while the service works | Built | `runner.handle_approval`, `handle_backend_event`. |
| Files delivered in the conversation | Yours | `slack_upload_mt_result`. |
| Feedback buttons | Not built | |
| **2. The next day** | Partly built | Sessions persist for seven days and wake on backend events. `verify:human_verification:completed` is not mapped in `adapters/events.py` yet. |
| **3. Jobs, in Japanese** | Built | Replies follow the person's language (`core/prompt.py`; evals in ja, de, pt, fr). |
| Job look-ups | Built | `get_job`, `list_jobs` over `RayService`. No task card for simple look-ups. |
| Approve a waiting job quote from the conversation | Yours | Your `JobQuotedMessage` buttons. The agent only tells the person it is waiting. |
| **4. In a channel** | Posting built; attribution and Remove not | `app_mention` reaches the seam with `use_thread=True`; `post_translation_in_thread` runs without a click. Attribution and the requester-only Remove need a change to your `slack:direct:mt:result` handler. |
| **5. Suggestion by DM** | Rules built, trigger not | `core/suggestions.py`, `slack_ui.suggestion_blocks`. |
| **6. Hand-off to a form** | Built | `offer_form` posts a button carrying your existing `action_id` and payload shape (`slack_ui.FORM_ACTION_IDS`). Your openers, and your trigger-safety pattern, do the rest. `new_job` has no button opener, so it gets text only. |
| **7. When things go wrong** | | |
| Expired quote | Yours, plus not built | The expiry message is your existing copy. Automatic re-quote with the difference is not built. |
| Stop | Built | `bolt.agent_session_stopped`, `runner.handle_stop`. |
| Model unavailable | Built | `runner` fallback plus `bolt.agent_quick_*`. |
| **8. IBM workspace** | Built, reusing yours | No connect or money talk: `core/prompt.py` plus the reply guard in `runner._guard`. People who cannot see quotes get your form. Admin-only suggestions: `suggestions.can_enable`. |
| **9. Home tab** | Blocks built, not wired | `slack_ui.home_blocks`. "Waiting on you", default languages: planned. Spend policy: a concept only. |
