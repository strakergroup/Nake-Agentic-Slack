import pytest

from app.agent.core.approvals import ApprovalError, ApprovalGate
from tests.agent.fakes import FixedClock, make_facts, make_session

GATED = {"post_translation_publicly"}


def gate(clock=None, **kwargs):
    return ApprovalGate(clock or FixedClock(), is_gated=lambda n: n in GATED, **kwargs)


def create(g, session, **overrides):
    args = dict(tool="post_translation_publicly", tool_input={"target_language": "ja"},
                requested_by="U_MIKA", summary="Post in Japanese")
    args.update(overrides)
    return g.create(session, **args)


def test_create_stores_a_long_random_id_with_an_expiry():
    clock, session = FixedClock(), make_session()
    approval = create(gate(clock, ttl_seconds=900), session)
    assert len(approval.id) >= 22  # 128 bits or more, url-safe
    assert approval.expires_at == clock.now + 900
    assert session.pending[approval.id] is approval


def test_ids_are_unique():
    g, session = gate(), make_session()
    assert len({create(g, session).id for _ in range(50)}) == 50


def test_verify_passes_for_the_requester_in_time():
    g, session = gate(), make_session()
    approval = create(g, session)
    assert g.verify(session, approval.id, "U_MIKA") == approval


def test_a_different_person_cannot_approve_even_an_admin():
    g, session = gate(), make_session()
    approval = create(g, session)
    with pytest.raises(ApprovalError) as err:
        g.verify(session, approval.id, "U_ADMIN")
    assert err.value.reason == "wrong_user"


def test_expired_one_second_after_the_limit():
    clock = FixedClock()
    g, session = gate(clock, ttl_seconds=900), make_session()
    approval = create(g, session)
    clock.advance(900)
    g.verify(session, approval.id, "U_MIKA")
    clock.advance(1)
    with pytest.raises(ApprovalError) as err:
        g.verify(session, approval.id, "U_MIKA")
    assert err.value.reason == "expired"


def test_unknown_id_and_id_from_another_session():
    g = gate()
    mine, other = make_session(), make_session(facts=make_facts(thread_ts="999.000"))
    theirs = create(g, other)
    for approval_id in ("nope", theirs.id):
        with pytest.raises(ApprovalError) as err:
            g.verify(mine, approval_id, "U_MIKA")
        assert err.value.reason == "unknown"


def test_an_approval_smuggled_onto_another_session_is_still_unknown():
    g = gate()
    mine, other = make_session(), make_session(facts=make_facts(thread_ts="999.000"))
    theirs = create(g, other)
    mine.pending[theirs.id] = theirs
    with pytest.raises(ApprovalError) as err:
        g.verify(mine, theirs.id, "U_MIKA")
    assert err.value.reason == "unknown"


def test_a_double_click_cannot_run_twice():
    g, session = gate(), make_session()
    approval = create(g, session)
    g.consume(session, approval.id, "U_MIKA")
    with pytest.raises(ApprovalError) as err:
        g.consume(session, approval.id, "U_MIKA")
    assert err.value.reason == "already_used"


def test_stopped_session_refuses_old_approvals():
    g, session = gate(), make_session()
    approval = create(g, session)
    session.stopped = True
    with pytest.raises(ApprovalError) as err:
        g.verify(session, approval.id, "U_MIKA")
    assert err.value.reason == "stopped"


def test_consume_returns_the_stored_input_not_what_the_click_carried():
    g, session = gate(), make_session()
    original = {"target_language": "ja"}
    approval = create(g, session, tool_input=original)
    original["target_language"] = "tampered"
    assert g.consume(session, approval.id, "U_MIKA").tool_input == {"target_language": "ja"}


def test_lookup_tools_cannot_get_approvals():
    with pytest.raises(ValueError):
        create(gate(), make_session(), tool="get_job")


def test_hidden_amounts_never_leak():
    approval = create(gate(), make_session(), show_amount=False, amount_text="USD 12.00")
    assert approval.amount_text is None


def test_decline_uses_up_the_approval():
    g, session = gate(), make_session()
    approval = create(g, session)
    g.decline(session, approval.id, "U_MIKA")
    with pytest.raises(ApprovalError) as err:
        g.consume(session, approval.id, "U_MIKA")
    assert err.value.reason == "already_used"


def test_cancel_all_clears_pending():
    g, session = gate(), make_session()
    approval = create(g, session)
    g.cancel_all(session)
    assert session.pending == {}
    with pytest.raises(ApprovalError) as err:
        g.verify(session, approval.id, "U_MIKA")
    assert err.value.reason == "already_used"
