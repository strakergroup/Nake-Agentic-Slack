import pytest

from app.agent.core import copy
from app.agent.core.approvals import ApprovalGate
from app.agent.core.audit import ListAuditSink
from app.agent.core.llm import LlmStep, LlmUnavailable
from app.agent.core.runner import AgentRunner
from app.agent.core.session import InMemorySessionStore
from app.agent.core.tools import ToolRegistry
from app.agent.core.types import ToolCall, ToolOutcome, session_key
from tests.agent.fakes import FixedClock, RecordingSlack, ScriptedLlm, make_facts, ok


def states(session):
    return {c["title"]: c["state"] for c in session.plan}


def say(text, reason="end_turn"):
    return LlmStep(
        [{"type": "text", "text": text}], [], text, reason, 100, 20, "claude-opus-5"
    )


def call(*calls):
    content = [
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
        for c in calls
    ]
    return LlmStep(content, list(calls), "", "tool_use", 100, 20, "claude-opus-5")


class Rig:
    def __init__(self, steps, handlers=None, facts=None, max_steps=8):
        self.facts = facts or make_facts()
        self.clock = FixedClock()
        self.llm, self.slack = ScriptedLlm(steps), RecordingSlack()
        self.store, self.audit = InMemorySessionStore(), ListAuditSink()
        self.tools = ToolRegistry()
        self.ran: list[tuple[str, dict]] = []
        for name, outcome in (handlers or {}).items():
            self.tools.bind(name, self._handler(name, outcome))
        gate = ApprovalGate(self.clock, is_gated=self.tools.is_gated)
        self.runner = AgentRunner(
            self.llm,
            self.slack,
            self.store,
            self.tools,
            gate,
            self.clock,
            self.audit,
            max_steps,
        )

    def _handler(self, name, outcome):
        async def handler(facts, tool_input):
            self.ran.append((name, tool_input))
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        return handler

    async def session(self):
        return await self.store.load(session_key(self.facts))

    async def say(self, text="Where's my job?", event_id="Ev1", files=None):
        await self.runner.handle_message(self.facts, text, event_id, files)


POST = ToolCall(
    "tu_post",
    "post_translation_publicly",
    {"target_language": "ja", "message_ts": "5.6"},
)


@pytest.mark.asyncio
async def test_plain_answer_streams_without_a_disclaimer_and_ends_active():
    rig = Rig([say("You have two open jobs.")])
    await rig.say()
    assert rig.slack.names() == ["set_status", "stream_start", "stream_stop"]
    assert rig.slack.calls[0][1] == {"status": "processing", "title": "Where's my job?"}
    stop = rig.slack.calls[-1][1]
    assert stop["text"] == "You have two open jobs."  # a status answer is not AI output
    assert stop["session_status"] == "active"
    assert [t.outcome for t in rig.audit.turns] == ["success"]
    assert rig.audit.turns[0].input_tokens == 100


@pytest.mark.asyncio
async def test_disclaimer_only_on_replies_that_deliver_ai_output():
    translate = ToolCall(
        "t1",
        "translate_text",
        {"target_language": "ja", "text": "Hello", "use_current_thread": False},
    )
    rig = Rig(
        [call(translate), say("I've requested the Japanese version.")],
        handlers={"translate_text": ok("requested")},
    )
    await rig.say("Put this in Japanese: Hello")
    assert copy.DISCLAIMER in rig.slack.calls[-1][1]["text"]


@pytest.mark.asyncio
async def test_lookup_runs_immediately_and_result_returns_to_the_model():
    rig = Rig(
        [call(ToolCall("tu_1", "list_jobs", {"filter": "open"})), say("Two jobs.")],
        handlers={"list_jobs": ok('[{"id": "TJ48213"}]', card_detail="2 found")},
    )
    await rig.say()
    assert rig.ran == [("list_jobs", {"filter": "open"})]
    second_request = rig.llm.requests[1]["messages"]
    assert second_request[-1] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "tu_1",
                "content": '[{"id": "TJ48213"}]',
            }
        ],
    }
    assert (await rig.session()).plan == []  # a simple look-up shows status only
    assert "stream_tasks" not in rig.slack.names()
    assert rig.audit.turns[0].tools_called == ["list_jobs"]


@pytest.mark.asyncio
async def test_two_lookups_in_one_step_return_in_one_message():
    rig = Rig(
        [
            call(
                ToolCall("a", "list_jobs", {"filter": "open"}),
                ToolCall("b", "account_status", {}),
            ),
            say("Done."),
        ],
        handlers={"list_jobs": ok("[]"), "account_status": ok("ready")},
    )
    await rig.say()
    results = rig.llm.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]


@pytest.mark.asyncio
async def test_gated_tool_does_not_run_and_waits_for_a_click():
    rig = Rig(
        [call(POST), say("The post is ready for your approval.")],
        handlers={"post_translation_publicly": ok("posted")},
    )
    await rig.say("Post this in Japanese")
    assert rig.ran == []
    session = await rig.session()
    assert len(session.pending) == 1 and session.status == "suspended"
    told = rig.llm.requests[1]["messages"][-1]["content"][0]["content"]
    assert told == copy.WAITING_FOR_APPROVAL
    stop = rig.slack.calls[-1][1]
    assert stop["session_status"] == "suspended"
    assert [b["action_id"] for b in stop["blocks"][1]["elements"]] == [
        "agent_approve",
        "agent_decline",
    ]


@pytest.mark.asyncio
async def test_model_claiming_it_posted_does_not_make_it_so():
    rig = Rig(
        [call(POST), say("Done, I have posted it.")],
        handlers={"post_translation_publicly": ok("posted")},
    )
    await rig.say("Post this in Japanese")
    assert rig.ran == []


@pytest.mark.asyncio
async def test_valid_click_runs_the_stored_action_once_and_resumes():
    rig = Rig(
        [
            call(POST),
            say("Waiting on you."),
            say("Arbitr has posted the Japanese version."),
        ],
        handlers={"post_translation_publicly": ok("posted", card_detail="Japanese")},
    )
    await rig.say("Post this in Japanese")
    approval_id = next(iter((await rig.session()).pending))
    await rig.runner.handle_approval(rig.facts, approval_id, "U_MIKA", approved=True)
    assert rig.ran == [
        ("post_translation_publicly", {"target_language": "ja", "message_ts": "5.6"})
    ]
    session = await rig.session()
    assert session.pending == {} and session.status == "active"
    assert [(c["title"], c["state"]) for c in session.plan] == [
        (copy.CARD_POST, "complete")
    ]
    assert (
        rig.slack.names().count("stream_start") == 2
    )  # request and execution are two messages
    await rig.runner.handle_approval(rig.facts, approval_id, "U_MIKA", approved=True)
    assert len(rig.ran) == 1
    assert rig.slack.calls[-1] == (
        "post_private",
        {"text": copy.APPROVAL_ERRORS["already_used"], "blocks": None},
    )


@pytest.mark.asyncio
async def test_wrong_person_gets_private_copy_and_nothing_runs():
    rig = Rig(
        [call(POST), say("Waiting on you.")],
        handlers={"post_translation_publicly": ok("posted")},
    )
    await rig.say("Post this in Japanese")
    approval_id = next(iter((await rig.session()).pending))
    await rig.runner.handle_approval(
        rig.facts, approval_id, "U_SOMEONE_ELSE", approved=True
    )
    assert rig.ran == []
    assert rig.slack.calls[-1] == (
        "post_private",
        {"text": copy.APPROVAL_ERRORS["wrong_user"], "blocks": None},
    )
    assert len((await rig.session()).pending) == 1


@pytest.mark.asyncio
async def test_decline_runs_nothing_and_says_so():
    rig = Rig(
        [call(POST), say("Waiting on you.")],
        handlers={"post_translation_publicly": ok("posted")},
    )
    await rig.say("Post this in Japanese")
    approval_id = next(iter((await rig.session()).pending))
    await rig.runner.handle_approval(rig.facts, approval_id, "U_MIKA", approved=False)
    assert rig.ran == []
    assert ("post", {"text": copy.DECLINED, "blocks": None}) in rig.slack.calls
    session = await rig.session()
    assert session.status == "active"
    assert session.plan == []  # a declined step is removed, never shown as done


@pytest.mark.asyncio
async def test_duplicate_event_does_nothing():
    rig = Rig([say("First.")])
    await rig.say(event_id="Ev1")
    before = len(rig.slack.calls)
    await rig.say(event_id="Ev1")
    assert len(rig.slack.calls) == before and len(rig.llm.requests) == 1


@pytest.mark.asyncio
async def test_model_down_gives_fixed_copy_and_quick_actions():
    rig = Rig([LlmUnavailable("down")])
    await rig.say()
    name, args = rig.slack.calls[1]
    assert name == "post" and args["text"] == copy.FALLBACK_MODEL_DOWN
    assert [b["action_id"] for b in args["blocks"][0]["elements"]] == [
        "agent_quick_jobs",
        "agent_quick_new",
        "agent_quick_help",
    ]
    assert rig.slack.calls[-1] == ("set_status", {"status": "active", "title": None})
    turn = rig.audit.turns[0]
    assert (turn.outcome, turn.error_type) == ("failure", "llm_error")


@pytest.mark.asyncio
async def test_tool_error_is_reported_to_the_model_once_then_fixed_copy():
    boom = RuntimeError("db password=hunter2 leaked in trace")
    job = ToolCall("t1", "get_job", {"job_id": "TJ1"})
    retry = ToolCall("t2", "get_job", {"job_id": "TJ1"})
    rig = Rig([call(job), call(retry)], handlers={"get_job": boom})
    await rig.say()
    first_result = rig.llm.requests[1]["messages"][-1]["content"][0]
    assert first_result["is_error"] is True and "hunter2" not in first_result["content"]
    stop = rig.slack.calls[-1][1]
    assert stop["text"] == copy.FALLBACK_TOOL_FAILED
    assert all("hunter2" not in str(args) for _, args in rig.slack.calls)
    assert rig.audit.turns[0].error_type == "tool_error"


@pytest.mark.asyncio
async def test_stop_cancels_approvals_and_old_clicks_are_refused():
    rig = Rig(
        [call(POST), say("Waiting on you.")],
        handlers={"post_translation_publicly": ok("posted")},
    )
    await rig.say("Post this in Japanese")
    approval_id = next(iter((await rig.session()).pending))
    await rig.runner.handle_stop(rig.facts)
    session = await rig.session()
    assert session.stopped and session.pending == {} and session.status == "active"
    assert all(c["state"] == "complete" for c in session.plan)
    assert ("post", {"text": copy.STOPPED, "blocks": None}) in rig.slack.calls
    await rig.runner.handle_approval(rig.facts, approval_id, "U_MIKA", approved=True)
    assert rig.ran == []


@pytest.mark.asyncio
async def test_a_stop_request_halts_a_running_turn_between_steps():
    rig = Rig(
        [call(ToolCall("t1", "list_jobs", {"filter": "open"})), say("never reached")],
        handlers={"list_jobs": ok("[]")},
    )

    original = rig.tools.handler_for("list_jobs")

    async def stopping_handler(facts, tool_input):
        await rig.store.request_stop(session_key(facts))
        return await original(facts, tool_input)

    rig.tools.bind("list_jobs", stopping_handler)
    await rig.say()
    assert len(rig.llm.requests) == 1
    assert rig.audit.turns[0].outcome == "partial"


@pytest.mark.asyncio
async def test_the_loop_is_bounded():
    steps = [call(ToolCall(f"t{i}", "account_status", {})) for i in range(10)]
    rig = Rig(steps, handlers={"account_status": ok("ready")}, max_steps=8)
    await rig.say()
    assert len(rig.llm.requests) == 8
    assert rig.slack.calls[-1][1]["text"] == copy.FALLBACK_TOO_MANY_STEPS
    assert (await rig.session()).messages[-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_ibm_replies_never_mention_connection_or_money():
    rig = Rig(
        [say("First, connect your account, then top up your balance.")],
        facts=make_facts(is_ibm=True),
    )
    await rig.say("How do I start?")
    assert rig.slack.calls[-1][1]["text"] == copy.HELP_IBM_GENERIC
    assert rig.audit.turns[0].outcome == "partial"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "leak", ["That will be USD 12.00.", "About $12 in total.", "It costs 120 credits."]
)
async def test_people_who_cannot_see_quotes_never_get_an_amount(leak):
    rig = Rig([say(leak)], facts=make_facts(can_see_quotes=False))
    await rig.say("How much?")
    assert rig.slack.calls[-1][1]["text"] == copy.HELP_NO_PRICE


@pytest.mark.asyncio
async def test_quote_tool_is_neither_offered_nor_runnable_without_quote_rights():
    quote = ToolCall(
        "t1",
        "request_document_quote",
        {"target_languages": ["ja"], "source_language": None},
    )
    rig = Rig(
        [call(quote), say("Use the form.")],
        handlers={"request_document_quote": ok("requested")},
        facts=make_facts(can_see_quotes=False),
    )
    await rig.say("Translate this deck")
    assert "request_document_quote" not in {
        t.name for t in rig.llm.requests[0]["tools"]
    }
    assert rig.ran == []
    assert rig.llm.requests[1]["messages"][-1]["content"][0]["is_error"] is True


@pytest.mark.asyncio
async def test_unknown_tool_is_an_error_result_not_a_crash():
    rig = Rig([call(ToolCall("t1", "delete_everything", {})), say("I cannot do that.")])
    await rig.say()
    assert rig.llm.requests[1]["messages"][-1]["content"][0]["is_error"] is True


@pytest.mark.asyncio
async def test_files_reach_the_model_as_metadata_only():
    rig = Rig([say("Noted.")])
    await rig.say(
        "Translate this",
        files=[
            {
                "id": "F1",
                "title": "Q3-launch.pptx",
                "filetype": "pptx",
                "size": 2_200_000,
                "url_private": "https://files.slack.com/secret",
                "preview": "CONFIDENTIAL",
            }
        ],
    )
    sent = rig.llm.requests[0]["messages"][0]["content"]
    assert "Q3-launch.pptx (pptx, 2.1 MB)" in sent
    assert "secret" not in sent and "CONFIDENTIAL" not in sent


@pytest.mark.asyncio
async def test_backend_events_update_cards_and_status_without_calling_the_model():
    quote = ToolCall(
        "t1",
        "request_document_quote",
        {"target_languages": ["ja"], "source_language": None},
    )
    rig = Rig(
        [call(quote), say("Arbitr has requested the quote.")],
        handlers={
            "request_document_quote": ToolOutcome(
                "requested",
                card_detail=copy.CARD_DETAIL_REQUESTED,
                waits_for_backend=True,
            )
        },
    )
    await rig.say("Translate this deck")
    key = session_key(rig.facts)
    assert states(await rig.session())[copy.CARD_PRICE] == "in_progress"
    await rig.runner.handle_backend_event(key, "quote_ready")
    session = await rig.session()
    assert (
        states(session)[copy.CARD_PRICE] == "complete" and session.status == "suspended"
    )
    await rig.runner.handle_backend_event(key, "delivered")
    session = await rig.session()
    assert (
        states(session)[copy.CARD_DELIVER] == "complete" and session.status == "active"
    )
    assert len(rig.llm.requests) == 2
    await rig.runner.handle_backend_event(
        "T1:D1:missing", "delivered"
    )  # unknown session: no crash


@pytest.mark.asyncio
async def test_voice_violations_are_recorded_not_blocked():
    rig = Rig([say("Great news! Your file is ready.")])
    await rig.say()
    assert "Great news!" in rig.slack.calls[-1][1]["text"]
    assert rig.audit.turns[0].voice_violations == ["no-exclamation"]


@pytest.mark.asyncio
async def test_long_turns_resend_processing_before_the_hour():
    rig = Rig(
        [call(ToolCall("t1", "account_status", {})), say("Ready.")],
        handlers={"account_status": ok("ready")},
    )
    original = rig.tools.handler_for("account_status")

    async def slow(facts, tool_input):
        rig.clock.advance(51 * 60)
        return await original(facts, tool_input)

    rig.tools.bind("account_status", slow)
    await rig.say()
    statuses = [
        args["status"] for name, args in rig.slack.calls if name == "set_status"
    ]
    assert statuses == ["processing", "processing"]


@pytest.mark.asyncio
async def test_second_message_continues_the_same_session():
    rig = Rig([say("Two jobs."), say("One quote is waiting.")])
    await rig.say("Where's my job?", event_id="Ev1")
    await rig.say("What's waiting on me?", event_id="Ev2")
    roles = [m["role"] for m in rig.llm.requests[1]["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert rig.slack.calls[0][1]["title"] == "Where's my job?"
    second_status = [args for name, args in rig.slack.calls if name == "set_status"][1]
    assert second_status["title"] is None


@pytest.mark.asyncio
async def test_refusal_gets_fixed_copy():
    rig = Rig([say("", reason="refusal")])
    await rig.say("Do something harmful")
    assert rig.slack.calls[-1][1]["text"] == copy.FALLBACK_REFUSED


SUBMIT = ToolCall(
    "tu_submit",
    "submit_document_translation",
    {
        "target_languages": ["ja", "de"],
        "target_language_names": ["Japanese", "German"],
        "source_language": "en",
    },
)


@pytest.mark.asyncio
async def test_a_person_who_cannot_see_quotes_confirms_with_one_click_and_no_price():
    rig = Rig(
        [call(SUBMIT), say("The button is waiting."), say("Started.")],
        handlers={
            "submit_document_translation": ok(
                "submitted", card_detail=copy.CARD_DETAIL_HANDED_OVER
            )
        },
        facts=make_facts(can_see_quotes=False),
    )
    await rig.say("Translate this deck into Japanese and German")
    assert rig.ran == []  # nothing is billed until the click
    stop = rig.slack.calls[-1][1]
    summary = stop["blocks"][0]["text"]["text"]
    assert summary == copy.SUBMIT_DOCUMENT_PROMPT.format(languages="Japanese, German")
    assert "USD" not in str(stop["blocks"]) and "$" not in str(stop["blocks"])
    assert (
        stop["blocks"][1]["elements"][0]["text"]["text"] == copy.SUBMIT_DOCUMENT_APPROVE
    )
    approval_id = next(iter((await rig.session()).pending))
    await rig.runner.handle_approval(rig.facts, approval_id, "U_MIKA", approved=True)
    assert [name for name, _ in rig.ran] == ["submit_document_translation"]
    session = await rig.session()
    assert [(c["title"], c["state"], c["detail"]) for c in session.plan] == [
        (copy.CARD_DELIVER, "complete", copy.CARD_DETAIL_HANDED_OVER)
    ]
    assert (
        session.status == "active"
    )  # handed to the service; the session does not sit on Working


@pytest.mark.asyncio
async def test_the_confirm_and_submit_tool_refuses_people_who_see_quotes():
    rig = Rig(
        [call(SUBMIT), say("Use the quote.")],
        handlers={"submit_document_translation": ok("submitted")},
    )
    await rig.say("Translate this deck")
    assert rig.ran == [] and (await rig.session()).pending == {}
    assert rig.llm.requests[1]["messages"][-1]["content"][0]["is_error"] is True


IN_THREAD = ToolCall(
    "tu_thread",
    "post_translation_in_thread",
    {"target_language": "ja", "message_ts": None},
)


@pytest.mark.asyncio
async def test_an_explicit_in_thread_request_posts_without_a_click():
    rig = Rig(
        [call(IN_THREAD), say("Posted the Japanese version in this thread.")],
        handlers={"post_translation_in_thread": ok("requested")},
        facts=make_facts(surface="mention", channel_id="C1"),
    )
    await rig.say("@Arbitr post this in Japanese for the Tokyo team")
    assert [name for name, _ in rig.ran] == ["post_translation_in_thread"]
    assert (await rig.session()).pending == {}
    assert copy.DISCLAIMER in rig.slack.calls[-1][1]["text"]


@pytest.mark.asyncio
async def test_in_thread_posting_is_refused_outside_a_channel_thread():
    rig = Rig(
        [call(IN_THREAD), say("I need a click for that.")],
        handlers={"post_translation_in_thread": ok("requested")},
    )
    await rig.say("post this in Japanese in #launch-global")
    assert rig.ran == []
    assert "post_translation_in_thread" not in {
        t.name for t in rig.llm.requests[0]["tools"]
    }
