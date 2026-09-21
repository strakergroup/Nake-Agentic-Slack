import json

from app.agent.core import copy, slack_ui
from app.agent.core.types import PendingApproval
from app.agent.core.voice import check_copy
from tests.agent.fakes import make_facts


def approval(**overrides):
    base = dict(id="appr_1", session_key="T1:C1:1.2", tool="post_translation_publicly", tool_input={"secret": "x"},
                requested_by="U_MIKA", summary=copy.POST_PUBLICLY_PROMPT, show_amount=False, amount_text=None,
                created_at=1.0, expires_at=2.0)
    base.update(overrides)
    return PendingApproval(**base)


def all_text(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for k, v in value.items() if k not in {"action_id", "block_id", "value", "type", "style"} for t in all_text(v)]
    if isinstance(value, list):
        return [t for v in value for t in all_text(v)]
    return []


def test_status_payload_has_title_only_when_given():
    facts = make_facts()
    assert slack_ui.status_payload(facts, "processing") == {"channel_id": "D1", "thread_ts": "111.222", "status": "processing"}
    assert slack_ui.status_payload(facts, "processing", "Q3 deck")["title"] == "Q3 deck"


def test_stream_start_names_the_recipient_outside_a_dm():
    dm = slack_ui.stream_start_payload(make_facts(surface="dm"), "")
    assert dm["task_display_mode"] == "plan" and "recipient_user_id" not in dm and "chunks" not in dm
    channel = slack_ui.stream_start_payload(make_facts(surface="mention", channel_id="C1"), "Hello")
    assert channel["recipient_user_id"] == "U_MIKA" and channel["recipient_team_id"] == "T1"
    assert channel["chunks"][0]["markdown_text"] == "Hello"


def test_task_chunks_map_card_states():
    plan = [
        {"id": "c1", "title": "Look up your jobs", "state": "complete", "detail": "2 found"},
        {"id": "c2", "title": "Your approval", "state": "waiting", "detail": ""},
        {"id": "c3", "title": "Post the translation", "state": "in_progress", "detail": ""},
    ]
    chunks = slack_ui.task_chunks(plan)
    assert [c["task"]["status"] for c in chunks] == ["complete", "pending", "in_progress"]
    assert chunks[0]["task"]["output"]["elements"][0]["elements"][0]["text"] == "2 found"
    assert "output" not in chunks[1]["task"]


def test_approval_button_carries_only_the_id():
    blocks = slack_ui.approval_blocks(approval(), copy.POST_PUBLICLY_APPROVE)
    buttons = blocks[1]["elements"]
    assert [b["action_id"] for b in buttons] == ["agent_approve", "agent_decline"]
    assert all(b["value"] == "appr_1" for b in buttons)
    assert "secret" not in json.dumps(blocks)
    assert buttons[0]["style"] == "primary" and "style" not in buttons[1]


def test_amount_only_shown_when_allowed():
    hidden = slack_ui.approval_blocks(approval(show_amount=False, amount_text=None))
    shown = slack_ui.approval_blocks(approval(show_amount=True, amount_text="USD 12.00"))
    assert "USD" not in json.dumps(hidden)
    assert "USD 12.00" in shown[0]["text"]["text"]


def test_handoff_reuses_the_existing_openers_and_their_payload_shape():
    files = [{"id": "F1", "title": "town-hall.mp4", "extra": "dropped"}]
    blocks = slack_ui.handoff_blocks("media", files, "D1", "111.222")
    button = blocks[1]["elements"][0]
    assert button["action_id"] == "video_transcribe_translate"
    assert json.loads(button["value"]) == {"files": [{"id": "F1", "title": "town-hall.mp4"}], "channel_id": "D1", "thread_ts": "111.222"}
    assert slack_ui.handoff_blocks("document_translation", files, "D1", None)[1]["elements"][0]["action_id"] == "document_mt_job"


def test_a_form_without_a_button_opener_gets_text_only():
    assert len(slack_ui.handoff_blocks("new_job", [], "D1", None)) == 1


def test_every_form_has_a_mapping():
    assert set(slack_ui.FORM_ACTION_IDS) == set(copy.HANDOFF_INTRO)


def test_suggestion_has_exactly_three_buttons_and_is_marked_private():
    blocks = slack_ui.suggestion_blocks("s1", "Japanese")
    assert blocks[0]["elements"][0]["text"] == "Only visible to you"
    assert [b["action_id"] for b in blocks[2]["elements"]] == ["agent_suggestion_act", "agent_suggestion_later", "agent_suggestion_never"]


def test_home_blocks_hide_channel_list_from_non_admins():
    muted = [{"label": "#launch-global, translation offers", "channel_id": "C1"}]
    plain = slack_ui.home_blocks("off", muted, None)
    admin = slack_ui.home_blocks("off", muted, [{"label": "#launch-global, on", "channel_id": "C1"}])
    assert copy.HOME_CHANNELS_TITLE not in json.dumps(plain)
    assert copy.HOME_CHANNELS_TITLE in json.dumps(admin)


def test_all_builder_text_follows_the_voice_rules_and_uses_no_emoji():
    samples = [
        slack_ui.approval_blocks(approval()), slack_ui.quick_action_blocks(), slack_ui.connect_blocks(),
        slack_ui.suggestion_blocks("s1", "Japanese"), slack_ui.admin_only_blocks(),
        slack_ui.digest_blocks(["2 delivered"]), slack_ui.home_blocks("daily", [], None),
        *[slack_ui.handoff_blocks(form, [], "D1", None) for form in slack_ui.FORM_ACTION_IDS],
    ]
    bad = [f"{v.rule}: {t}" for blocks in samples for t in all_text(blocks) for v in check_copy(t)]
    assert bad == []
    assert '"emoji": true' not in json.dumps(samples)
    assert all(len(blocks) <= 50 for blocks in samples)
