"""Tests for staged AI Translation quote adjustment helpers."""

from typing import Any

from app.slack.evaluation_ai_adjustment import (
    AI_QUOTE_EMBED_SELECTION_ACTION_ID,
    AI_QUOTE_LANGUAGE_SELECTION_ACTION_ID,
    EMBED_SOURCE_VALUE,
    colliding_evaluate_upload_filenames,
    estimated_pdf_file_language_costs,
    estimated_pdf_language_costs,
    file_language_pairs,
    filename_language_pairs_from_selection,
    filter_job_to_pairs,
    filter_language_costs_by_pairs,
    language_costs_with_cancelled_status,
    media_embed_toggles_from_view,
    pdf_adjusted_costs,
    pdf_costs_for_pairs,
    post_convert_filename_collision_message,
    quote_file_language_costs,
    quote_language_costs,
    quote_message_context_from_body,
    quote_tokens_for_pairs,
    selected_pairs_from_view,
    selected_values,
    update_modal_cost_blocks,
)
from app.slack.templates.blocks import evaluation_credits_quote_blocks
from app.slack.templates.views import evaluation_ai_quote_adjust_modal


def test_file_language_pairs_returns_stable_cross_product():
    assert file_language_pairs(["file-2", "file-1"], ["lang-2", "lang-1"]) == [
        "file-1:lang-1",
        "file-1:lang-2",
        "file-2:lang-1",
        "file-2:lang-2",
    ]


def test_quote_tokens_for_pairs_uses_exact_pair_details():
    quote = {
        "token": 999,
        "details": [
            {
                "file_uuid": "file-1",
                "target_language_uuid": "lang-1",
                "token": 10,
            },
            {
                "file_uuid": "file-1",
                "target_language_uuid": "lang-2",
                "token": 25,
            },
        ],
    }

    assert quote_tokens_for_pairs(quote, ["file-1:lang-2"]) == 25


def test_quote_language_costs_aggregates_all_files_per_language():
    quote = {
        "details": [
            {"target_language_uuid": "lang-1", "token": 10},
            {"target_language_uuid": "lang-1", "token": 15},
            {"target_language_uuid": "lang-2", "token": 20},
        ]
    }

    assert quote_language_costs(
        quote,
        [
            {"uuid": "lang-1", "name": "French"},
            {"uuid": "lang-2", "name": "German"},
        ],
    ) == [
        {"value": "lang-1", "label": "French", "token": 25},
        {"value": "lang-2", "label": "German", "token": 20},
    ]


def test_quote_file_language_costs_groups_rows_and_resolves_language_names():
    quote = {
        "details": [
            {
                "file_uuid": "file-1",
                "target_language_uuid": "lang-1",
                "token": 10,
            },
            {
                "file_uuid": "file-2",
                "target_language_uuid": "lang-1",
                "token": 15,
            },
        ]
    }
    job = {
        "source_files": [
            {"file_uuid": "file-1", "filename": "first.docx"},
            {"file_uuid": "file-2", "filename": "second.docx"},
        ],
        "target_languages": [{"uuid": "lang-1"}],
    }

    assert quote_file_language_costs(quote, job, {"lang-1": "French"}) == [
        {
            "file_uuid": "file-1",
            "file_label": "first.docx",
            "value": "lang-1",
            "label": "French",
            "token": 10,
        },
        {
            "file_uuid": "file-2",
            "file_label": "second.docx",
            "value": "lang-1",
            "label": "French",
            "token": 15,
        },
    ]


def test_pdf_adjusted_costs_filters_files_and_recalculates_pages():
    session = {
        "files": [
            {"id": "file-1", "character_count": 1000, "pdf_page_count": 2},
            {"id": "file-2", "character_count": 3000, "pdf_page_count": 5},
        ]
    }

    ai_tokens, page_count = pdf_adjusted_costs(
        session,
        ["file-1"],
        ["lang-1", "lang-2"],
    )

    assert ai_tokens == 4  # ceil(1000 * 2 * 0.002)
    assert page_count == 2


def test_pdf_language_rows_sum_to_the_aggregate_estimate():
    files = [{"id": "file-1", "character_count": 500}]
    languages = [
        {"value": "lang-1", "label": "French"},
        {"value": "lang-2", "label": "German"},
    ]

    language_costs = estimated_pdf_language_costs(files, languages)
    aggregate_tokens, _ = pdf_adjusted_costs(
        {"files": files},
        ["file-1"],
        ["lang-1", "lang-2"],
    )

    assert sum(row["token"] for row in language_costs) == aggregate_tokens


def test_pdf_file_language_rows_group_prices_by_filename():
    rows = estimated_pdf_file_language_costs(
        [{"id": "file-1", "title": "source.pdf", "character_count": 100}],
        [{"value": "lang-1", "label": "French"}],
    )

    assert rows == [
        {
            "file_uuid": "file-1",
            "file_label": "source.pdf",
            "value": "lang-1",
            "label": "French",
            "token": 1,
        }
    ]


def test_pdf_costs_for_pairs_uses_only_selected_file_language_rows():
    session = {
        "files": [
            {
                "id": "file-1",
                "size": 100,
                "character_count": 9999,
                "pdf_page_count": 1,
            },
            {
                "id": "file-2",
                "size": 100,
                "character_count": 9999,
                "pdf_page_count": 2,
            },
        ],
        "all_language_costs": [
            {
                "file_uuid": "file-1",
                "value": "lang-1",
                "token": 10,
            },
            {
                "file_uuid": "file-1",
                "value": "lang-2",
                "token": 11,
            },
            {
                "file_uuid": "file-2",
                "value": "lang-1",
                "token": 20,
            },
        ],
    }

    ai_tokens, pages = pdf_costs_for_pairs(
        session,
        ["file-1:lang-2", "file-2:lang-1"],
    )

    assert ai_tokens == 31
    assert pages == 3


def test_filter_language_costs_by_pairs_keeps_file_specific_rows():
    rows = [
        {"file_uuid": "file-1", "value": "lang-1", "token": 1},
        {"file_uuid": "file-2", "value": "lang-1", "token": 2},
    ]

    assert filter_language_costs_by_pairs(rows, ["file-2:lang-1"]) == [rows[1]]


def test_language_costs_with_cancelled_status_marks_deselected_rows():
    rows = [
        {"file_uuid": "file-1", "value": "lang-1", "label": "French", "token": 10},
        {"file_uuid": "file-1", "value": "lang-2", "label": "German", "token": 20},
    ]

    marked = language_costs_with_cancelled_status(rows, ["file-1:lang-1"])

    assert marked[0]["cancelled"] is False
    assert marked[1]["cancelled"] is True
    assert marked[1]["label"] == "German"


def test_language_costs_with_cancelled_status_marks_all_when_empty():
    rows = [
        {"file_uuid": "file-1", "value": "lang-1", "label": "French", "token": 10},
        {"file_uuid": "file-1", "value": "lang-2", "label": "German", "token": 20},
    ]

    marked = language_costs_with_cancelled_status(rows, [])

    assert all(row["cancelled"] is True for row in marked)


def test_filter_job_to_pairs_removes_unselected_rows_without_mutating_job():
    job = {
        "source_files": [
            {
                "file_uuid": "file-1",
                "target_files": [
                    {"language_uuid": "lang-1"},
                    {"language_uuid": "lang-2"},
                ],
            }
        ],
        "target_languages": [
            {"uuid": "lang-1"},
            {"uuid": "lang-2"},
        ],
    }

    filtered = filter_job_to_pairs(job, ["file-1:lang-2"])

    assert filtered["target_languages"] == [{"uuid": "lang-2"}]
    assert filtered["source_files"][0]["target_files"] == [{"language_uuid": "lang-2"}]
    assert len(job["source_files"][0]["target_files"]) == 2


def test_mark_out_of_scope_pairs_cancelled_keeps_languages_and_marks_rows():
    from app.slack.evaluation_ai_adjustment import mark_out_of_scope_pairs_cancelled

    job = {
        "source_files": [
            {
                "file_uuid": "file-1",
                "target_files": [
                    {"language_uuid": "lang-hi"},
                    {"language_uuid": "lang-ko"},
                ],
            },
            {
                "file_uuid": "file-2",
                "target_files": [
                    {"language_uuid": "lang-hi"},
                    {"language_uuid": "lang-ko"},
                ],
            },
        ],
        "target_languages": [
            {"uuid": "lang-hi", "name": "Hindi"},
            {"uuid": "lang-ko", "name": "Korean"},
            {"uuid": "lang-lo", "name": "Lao"},
        ],
    }

    marked = mark_out_of_scope_pairs_cancelled(
        job,
        ["file-1:lang-hi", "file-2:lang-ko"],
    )

    assert [lang["uuid"] for lang in marked["target_languages"]] == [
        "lang-hi",
        "lang-ko",
        "lang-lo",
    ]
    file_1 = marked["source_files"][0]["target_files"]
    file_2 = marked["source_files"][1]["target_files"]
    assert file_1[0] == {"language_uuid": "lang-hi"}
    assert file_1[1]["human_job_status"] == "Cancelled"
    assert file_1[2]["human_job_status"] == "Cancelled"
    assert file_2[0]["human_job_status"] == "Cancelled"
    assert file_2[1] == {"language_uuid": "lang-ko"}
    assert file_2[2]["human_job_status"] == "Cancelled"
    assert "human_job_status" not in job["source_files"][0]["target_files"][0]


def test_combined_quote_asymmetric_selection_shows_cancelled_not_zero():
    """Adjust/submit intermediary HT quote must not flash USD 0.00 ghost rows."""
    from app.slack.evaluation_ai_adjustment import mark_out_of_scope_pairs_cancelled
    from app.slack.evaluation_combined_quotes import (
        PRE_QE_QUOTE_DISPLAY,
        combined_human_job_quote_message,
    )

    job = {
        "uuid": "job-123",
        "workflow_uuid": "workflow-123",
        "target_languages": [
            {"uuid": "lang-fr", "name": "French"},
            {"uuid": "lang-de", "name": "German"},
        ],
        "source_files": [
            {
                "file_uuid": "file-a",
                "filename": "a.docx",
                "target_files": [
                    {"language_uuid": "lang-fr"},
                    {"language_uuid": "lang-de"},
                ],
                "report": {"language_uuid": "source-uuid"},
            },
            {
                "file_uuid": "file-b",
                "filename": "b.docx",
                "target_files": [
                    {"language_uuid": "lang-fr"},
                    {"language_uuid": "lang-de"},
                ],
                "report": {"language_uuid": "source-uuid"},
            },
        ],
    }
    selected = ["file-a:lang-fr", "file-b:lang-de"]
    costs = [
        {
            "file_uuid": "file-a",
            "language_uuid": "lang-fr",
            "service_list": [{"estimated_cost": 12.0, "time_estimate_days": 2}],
        },
        {
            "file_uuid": "file-b",
            "language_uuid": "lang-de",
            "service_list": [{"estimated_cost": 15.0, "time_estimate_days": 3}],
        },
    ]
    message = combined_human_job_quote_message(
        mark_out_of_scope_pairs_cancelled(job, selected),
        costs,
        qe_token_cost=0,
        qe_additional_costs=[],
        actions=False,
        status_message="Accepting quote...",
        allow_adjust=False,
        **PRE_QE_QUOTE_DISPLAY,
    )
    rendered = str(message.blocks)
    assert "USD 12.00" in rendered
    assert "USD 15.00" in rendered
    assert rendered.count(">Cancelled") == 2
    assert "USD 0.00" not in rendered


def test_ai_quote_blocks_show_adjust_button_and_exact_guidance():
    blocks = evaluation_credits_quote_blocks(
        "AI Translation",
        100,
        accept_action_id="accept",
        adjust_action_id="adjust",
        job_uuid="job-1",
    )
    rendered = str(blocks)

    assert "Adjust Request" in rendered
    assert (
        "Review the cost below. To continue preparing your human translation "
        "quote, click *Accept Quote* or click *Adjust Request* to remove "
        "languages and/or source files."
    ) in rendered


def test_document_mt_adjust_guidance_stays_generic():
    blocks = evaluation_credits_quote_blocks(
        "AI Translation",
        100,
        accept_action_id="accept",
        adjust_action_id="document_mt_quote_adjust",
        job_uuid="job-1",
        intro_text="Running the AI translation will incur the following cost:",
    )
    rendered = str(blocks)

    assert (
        "Review the cost below and click *Accept Quote* to continue, or "
        "*Adjust Request* to remove languages and/or source files."
    ) in rendered
    assert "preparing your human translation quote" not in rendered


def test_ai_adjust_modal_uses_independent_file_language_checkboxes():
    view = evaluation_ai_quote_adjust_modal(
        quote_id="job-1",
        quote_kind="evaluate",
        language_costs=[
            {
                "file_uuid": "file-1",
                "file_label": "first.docx",
                "value": "lang-1",
                "label": "French",
                "token": 25,
            },
            {
                "file_uuid": "file-2",
                "file_label": "second.docx",
                "value": "lang-1",
                "label": "French",
                "token": 25,
            },
        ],
        selected_pairs=["file-1:lang-1"],
        ai_tokens=25,
        channel_id="C1",
        message_ts="111.222",
    )
    first_language_element = view["blocks"][2]["elements"][0]
    second_language_element = view["blocks"][4]["elements"][0]

    assert first_language_element["options"][0]["value"] == "file-1:lang-1"
    assert second_language_element["options"][0]["value"] == "file-2:lang-1"
    assert (
        first_language_element["initial_options"] == first_language_element["options"]
    )
    assert "initial_options" not in second_language_element
    assert ":paperclip: *first.docx*" in str(view["blocks"])
    assert ":paperclip: *second.docx*" in str(view["blocks"])
    assert "independent per file" in str(view["blocks"])
    assert "*Total cost:* USD 0.50" in str(view["blocks"])


def test_ai_adjust_modal_shows_source_embed_row_when_tokens_passed():
    view = evaluation_ai_quote_adjust_modal(
        quote_id="job-1",
        quote_kind="media_translation",
        language_costs=[
            {
                "file_uuid": "file-1",
                "file_label": "clip.mp4",
                "value": "lang-1",
                "label": "French",
                "token": 300,
            },
        ],
        selected_pairs=["file-1:lang-1"],
        ai_tokens=300,
        source_embed_tokens=30,
        channel_id="C1",
        message_ts="111.222",
    )
    rendered = str(view["blocks"])
    assert "*Source subtitle embedding:* USD 0.60" in rendered
    assert "Translated subtitle embedding" not in rendered
    assert "*Total cost:* USD 6.60" in rendered


def test_ai_adjust_modal_omits_source_embed_row_by_default():
    view = evaluation_ai_quote_adjust_modal(
        quote_id="job-1",
        quote_kind="evaluate",
        language_costs=[
            {
                "file_uuid": "file-1",
                "file_label": "first.docx",
                "value": "lang-1",
                "label": "French",
                "token": 25,
            },
        ],
        selected_pairs=["file-1:lang-1"],
        ai_tokens=25,
        channel_id="C1",
        message_ts="111.222",
    )
    assert "Source subtitle embedding" not in str(view["blocks"])
    assert "AI Translation" not in str(view["blocks"])
    assert "ai_quote_translation_cost_block" not in str(view["blocks"])
    assert view["submit"]["text"] == "Accept Quote"
    assert '"message_ts": "111.222"' in view["private_metadata"]
    assert '"channel_id": "C1"' in view["private_metadata"]


def test_selected_pairs_from_view_reads_file_specific_checkbox_values():
    view = {
        "state": {
            "values": {
                "language-1": {
                    AI_QUOTE_LANGUAGE_SELECTION_ACTION_ID: {
                        "selected_options": [{"value": "file-1:lang-1"}]
                    }
                },
                "language-2": {
                    AI_QUOTE_LANGUAGE_SELECTION_ACTION_ID: {
                        "selected_options": [{"value": "file-2:lang-2"}]
                    }
                },
            }
        }
    }

    assert selected_pairs_from_view(view) == ["file-1:lang-1", "file-2:lang-2"]
    assert selected_values(view, AI_QUOTE_LANGUAGE_SELECTION_ACTION_ID) == [
        "file-1:lang-1",
        "file-2:lang-2",
    ]


def test_quote_message_context_from_body_reads_message_and_container():
    assert quote_message_context_from_body(
        {
            "channel": {"id": "C1"},
            "message": {"ts": "111.222"},
        }
    ) == ("C1", "111.222")
    assert quote_message_context_from_body(
        {
            "container": {"channel_id": "C2", "message_ts": "333.444"},
        }
    ) == ("C2", "333.444")


def test_filename_language_pairs_from_selection_uses_post_convert_names():
    assert filename_language_pairs_from_selection(
        [
            {"id": "F1", "title": "notes.txt"},
            {"id": "F2", "title": "receipt.pdf"},
        ],
        ["F1:lang-ja", "F2:lang-hu"],
    ) == ["notes.txt:lang-ja", "receipt.docx:lang-hu"]


def test_colliding_evaluate_upload_filenames_detects_pdf_docx_same_stem():
    assert colliding_evaluate_upload_filenames(
        [
            {"id": "F1", "title": "A great summer vacation.pdf"},
            {"id": "F2", "title": "A great summer vacation.docx"},
        ]
    ) == {
        "A great summer vacation.docx": [
            "A great summer vacation.pdf",
            "A great summer vacation.docx",
        ]
    }


def test_colliding_evaluate_upload_filenames_ignores_distinct_stems():
    assert (
        colliding_evaluate_upload_filenames(
            [
                {"id": "F1", "title": "a.pdf"},
                {"id": "F2", "title": "b.docx"},
            ]
        )
        == {}
    )


def test_post_convert_filename_collision_message_names_both_files():
    message = post_convert_filename_collision_message(
        {
            "report.docx": ["report.pdf", "report.docx"],
        }
    )
    assert "*report.pdf*" in message
    assert "*report.docx*" in message
    assert "Rename the duplicates" in message


def test_update_modal_cost_blocks_keeps_deselected_checkboxes_unchecked():
    option_one = {
        "text": {"type": "mrkdwn", "text": "*French*: USD 0.20"},
        "value": "file-1:lang-1",
    }
    option_two = {
        "text": {"type": "mrkdwn", "text": "*German*: USD 0.30"},
        "value": "file-2:lang-2",
    }
    view = {
        "type": "modal",
        "callback_id": "evaluation_ai_quote_adjust_submit",
        "title": {"type": "plain_text", "text": "Adjust Request"},
        "submit": {"type": "plain_text", "text": "Accept Quote"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "private_metadata": "{}",
        "blocks": [
            {
                "type": "actions",
                "block_id": "ai_quote_language_file-1_lang-1",
                "elements": [
                    {
                        "type": "checkboxes",
                        "action_id": AI_QUOTE_LANGUAGE_SELECTION_ACTION_ID,
                        "options": [option_one],
                        # Stale open-state selection that must not be replayed.
                        "initial_options": [option_one],
                    }
                ],
            },
            {
                "type": "actions",
                "block_id": "ai_quote_language_file-2_lang-2",
                "elements": [
                    {
                        "type": "checkboxes",
                        "action_id": AI_QUOTE_LANGUAGE_SELECTION_ACTION_ID,
                        "options": [option_two],
                        "initial_options": [option_two],
                    }
                ],
            },
            {
                "type": "section",
                "block_id": "total_cost_block",
                "text": {"type": "mrkdwn", "text": "*Total cost*: USD 0.50"},
            },
        ],
        "state": {
            "values": {
                "ai_quote_language_file-1_lang-1": {
                    AI_QUOTE_LANGUAGE_SELECTION_ACTION_ID: {
                        "selected_options": [{"value": "file-1:lang-1"}]
                    }
                },
                "ai_quote_language_file-2_lang-2": {
                    AI_QUOTE_LANGUAGE_SELECTION_ACTION_ID: {"selected_options": []}
                },
            }
        },
    }

    updated = update_modal_cost_blocks(view, ai_tokens=10, pdf_tokens=0)
    first = updated["blocks"][0]["elements"][0]
    second = updated["blocks"][1]["elements"][0]

    assert first["initial_options"] == [option_one]
    assert "initial_options" not in second
    assert "*Total cost:* USD 0.20" in updated["blocks"][2]["text"]["text"]
    assert "ai_quote_translation_cost_block" not in str(updated["blocks"])


def _embed_view(*selected: str) -> dict[str, Any]:
    return {
        "state": {
            "values": {
                "ai_quote_embed": {
                    AI_QUOTE_EMBED_SELECTION_ACTION_ID: {
                        "selected_options": [{"value": value} for value in selected]
                    }
                }
            }
        }
    }


def test_media_embed_toggles_from_view_reads_selection():
    assert media_embed_toggles_from_view(
        _embed_view(EMBED_SOURCE_VALUE, "Fmedia:es")
    ) == (True, {"Fmedia:es"})
    assert media_embed_toggles_from_view(_embed_view(EMBED_SOURCE_VALUE)) == (
        True,
        set(),
    )
    assert media_embed_toggles_from_view(_embed_view()) == (False, set())


def test_media_embed_toggles_from_view_missing_block_returns_none():
    assert media_embed_toggles_from_view({"state": {"values": {}}}) is None


def test_ai_adjust_modal_shows_embed_toggles_for_media():
    view = evaluation_ai_quote_adjust_modal(
        quote_id="job-1",
        quote_kind="media_translation",
        language_costs=[
            {
                "file_uuid": "file-1",
                "file_label": "clip.mp4",
                "value": "lang-1",
                "label": "French",
                "token": 300,
            },
        ],
        selected_pairs=["file-1:lang-1"],
        ai_tokens=300,
        source_embed_tokens=30,
        show_embed_toggles=True,
        embed_source=True,
        embed_languages=["lang-1"],
        channel_id="C1",
        message_ts="111.222",
    )
    toggles = [
        element
        for block in view["blocks"]
        for element in block.get("elements") or []
        if element.get("action_id") == AI_QUOTE_EMBED_SELECTION_ACTION_ID
    ]
    assert len(toggles) == 2
    per_language = toggles[0]
    assert [option["value"] for option in per_language["options"]] == ["file-1:lang-1"]
    assert per_language["initial_options"] == per_language["options"]
    source_toggle = toggles[1]
    assert [option["value"] for option in source_toggle["options"]] == [
        EMBED_SOURCE_VALUE
    ]
    assert source_toggle["initial_options"] == source_toggle["options"]
    assert "*Total cost:* USD 6.60" in str(view["blocks"])


def test_ai_adjust_modal_omits_embed_toggles_by_default():
    view = evaluation_ai_quote_adjust_modal(
        quote_id="job-1",
        quote_kind="evaluate",
        language_costs=[
            {
                "file_uuid": "file-1",
                "file_label": "first.docx",
                "value": "lang-1",
                "label": "French",
                "token": 25,
            },
        ],
        selected_pairs=["file-1:lang-1"],
        ai_tokens=25,
        channel_id="C1",
        message_ts="111.222",
    )
    assert AI_QUOTE_EMBED_SELECTION_ACTION_ID not in str(view["blocks"])


def test_update_modal_cost_blocks_syncs_embed_toggle_initials():
    from app.slack.evaluation_ai_adjustment import update_modal_cost_blocks

    option_source = {
        "text": {"type": "mrkdwn", "text": "*Source*"},
        "value": "embed_source",
    }
    option_translated = {
        "text": {"type": "mrkdwn", "text": "*Translated*"},
        "value": "embed_translated",
    }
    view = {
        "state": {
            "values": {
                "ai_quote_embed": {
                    AI_QUOTE_EMBED_SELECTION_ACTION_ID: {
                        "selected_options": [{"value": "embed_translated"}]
                    }
                }
            }
        },
        "blocks": [
            {
                "block_id": "ai_quote_embed_selection",
                "elements": [
                    {
                        "type": "checkboxes",
                        "options": [option_source, option_translated],
                        "action_id": AI_QUOTE_EMBED_SELECTION_ACTION_ID,
                        "initial_options": [option_source, option_translated],
                    }
                ],
            },
            {
                "block_id": "total_cost_block",
                "text": {"type": "mrkdwn", "text": "*Total cost:* USD 0.00"},
            },
        ],
    }

    updated = update_modal_cost_blocks(view, ai_tokens=0, pdf_tokens=0)

    toggles = updated["blocks"][0]["elements"][0]
    assert toggles["initial_options"] == [option_translated]
