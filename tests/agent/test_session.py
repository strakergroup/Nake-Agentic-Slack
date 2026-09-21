import asyncio

import fakeredis.aioredis
import pytest

from app.agent.core.session import (
    PREFIX,
    SESSION_TTL,
    InMemorySessionStore,
    RedisSessionStore,
    trim_history,
)
from app.agent.core.types import PendingApproval
from tests.agent.fakes import make_facts, make_session


@pytest.fixture(params=["memory", "redis"])
def store(request):
    if request.param == "memory":
        return InMemorySessionStore()
    return RedisSessionStore(fakeredis.aioredis.FakeRedis())


def full_session():
    session = make_session(title="Q3 deck", status="suspended")
    session.messages = [
        {"role": "user", "content": "Translate this"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "abc"},
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "get_job",
                    "input": {"job_id": "TJ1"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "{}"}
            ],
        },
    ]
    session.pending["a1"] = PendingApproval(
        id="a1",
        session_key=session.key,
        tool="post_translation_publicly",
        tool_input={"x": 1},
        requested_by="U_MIKA",
        summary="Post",
        show_amount=False,
        amount_text=None,
        created_at=1.0,
        expires_at=2.0,
        tool_use_id="tu_9",
    )
    session.plan = [
        {
            "id": "c1",
            "title": "Look up your jobs",
            "state": "complete",
            "detail": "2 found",
        }
    ]
    return session


@pytest.mark.asyncio
async def test_round_trip_keeps_everything(store):
    session = full_session()
    await store.save(session)
    loaded = await store.load(session.key)
    assert loaded == session
    assert loaded.facts.display_name == "Mika Kato"
    assert loaded.pending["a1"].tool_use_id == "tu_9"


@pytest.mark.asyncio
async def test_missing_session_is_none(store):
    assert await store.load("T1:D1:nope") is None


@pytest.mark.asyncio
async def test_redis_sessions_expire_in_seven_days():
    redis = fakeredis.aioredis.FakeRedis()
    store, session = RedisSessionStore(redis), full_session()
    await store.save(session)
    ttl = await redis.ttl(f"{PREFIX}session:{session.key}")
    assert SESSION_TTL - 5 <= ttl <= SESSION_TTL


@pytest.mark.asyncio
async def test_a_turn_can_be_claimed_once(store):
    assert await store.claim_turn("Ev123") is True
    assert await store.claim_turn("Ev123") is False
    assert await store.claim_turn("Ev124") is True


@pytest.mark.asyncio
async def test_quote_and_channel_lookups(store):
    await store.link_quote("q-1", "T1:D1:111.222")
    assert await store.session_for_quote("q-1") == "T1:D1:111.222"
    assert await store.session_for_quote("q-2") is None
    await store.remember_open("T1", "D1", "U_MIKA", "T1:D1:111.222")
    await store.remember_open("T1", "D1", "U_MIKA", "T1:D1:333.444")
    assert await store.session_for_channel_user("T1", "D1", "U_MIKA") == "T1:D1:333.444"
    assert await store.session_for_channel_user("T1", "D1", "U_OTHER") is None


@pytest.mark.asyncio
async def test_lock_serialises_two_turns_on_one_thread(store):
    order = []

    async def turn(name, hold):
        async with store.lock("T1:D1:111.222"):
            order.append(f"{name}-in")
            await asyncio.sleep(hold)
            order.append(f"{name}-out")

    await asyncio.gather(turn("a", 0.05), turn("b", 0.0))
    assert order in (
        ["a-in", "a-out", "b-in", "b-out"],
        ["b-in", "b-out", "a-in", "a-out"],
    )


def test_history_is_capped_without_splitting_a_tool_pair():
    messages = []
    for i in range(40):
        messages.append({"role": "user", "content": f"q{i}"})
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": f"t{i}", "name": "get_job", "input": {}}
                ],
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": f"t{i}", "content": "x"}
                ],
            }
        )
        messages.append(
            {"role": "assistant", "content": [{"type": "text", "text": f"a{i}"}]}
        )
    trimmed = trim_history(messages, limit=60)
    assert len(trimmed) <= 60
    assert trimmed[0]["role"] == "user" and isinstance(trimmed[0]["content"], str)
    used = {
        b["id"]
        for m in trimmed
        if m["role"] == "assistant"
        for b in m["content"]
        if b["type"] == "tool_use"
    }
    results = {
        b["tool_use_id"]
        for m in trimmed
        if m["role"] == "user" and isinstance(m["content"], list)
        for b in m["content"]
    }
    assert used == results


def test_short_history_is_untouched():
    messages = [{"role": "user", "content": "hi"}]
    assert trim_history(messages) is messages


def test_facts_survive_json():
    facts = make_facts(is_ibm=True, locale="ja-JP", enterprise_id="E1")
    session = make_session(facts=facts)
    from app.agent.core.types import Session

    assert Session.from_json(session.to_json()).facts == facts


@pytest.mark.asyncio
async def test_stop_flag_round_trip(store):
    key = "T1:D1:111.222"
    assert await store.stop_requested(key) is False
    await store.request_stop(key)
    assert await store.stop_requested(key) is True
    await store.clear_stop(key)
    assert await store.stop_requested(key) is False
