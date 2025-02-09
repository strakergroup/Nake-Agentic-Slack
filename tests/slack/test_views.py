import pytest
from app.slack.templates.views import calculate_total_cost, verify_job_modal
from unittest.mock import patch


@pytest.mark.parametrize(
    "languages,costs,expected",
    [
        # Test single selected language
        (
            [{"uuid": "lang1", "selected": True}],
            [{"language_uuid": "lang1", "cost": "10.50"}],
            10.50,
        ),
        # Test multiple selected languages
        (
            [{"uuid": "lang1", "selected": True}, {"uuid": "lang2", "selected": True}],
            [
                {"language_uuid": "lang1", "cost": "10.50"},
                {"language_uuid": "lang2", "cost": "15.75"},
            ],
            26.25,
        ),
        # Test with some unselected languages
        (
            [{"uuid": "lang1", "selected": True}, {"uuid": "lang2", "selected": False}],
            [
                {"language_uuid": "lang1", "cost": "10.50"},
                {"language_uuid": "lang2", "cost": "15.75"},
            ],
            10.50,
        ),
        # Test empty inputs
        ([], [], 0.0),
    ],
)
def test_calculate_total_cost(languages, costs, expected):
    assert calculate_total_cost(languages, costs) == expected


@patch("app.slack.templates.views.job_summary_string")
def test_verify_job_modal_multiple_languages(mock_summary):
    mock_summary.return_value = "Test summary"

    job = {
        "uuid": "job1",
        "target_languages": [
            {"uuid": "lang1", "name": "Spanish", "selected": True},
            {"uuid": "lang2", "name": "French", "selected": True},
        ],
        "source_files": [
            {
                "report": {
                    "language_uuid": "source1",
                    "evaluation_reports": [
                        {"target_language": "lang1"},
                        {"target_language": "lang2"},
                    ],
                },
                "target_files": [
                    {"language_uuid": "lang1"},
                    {"language_uuid": "lang2"},
                ],
            }
        ],
    }

    all_langs = [{"uuid": "source1", "name": "English"}]
    costs = [
        {"language_uuid": "lang1", "cost": "10.50"},
        {"language_uuid": "lang2", "cost": "15.75"},
    ]

    result = verify_job_modal(job, all_langs, costs)

    # Verify total cost section exists and shows correct amount
    total_cost_blocks = [
        block
        for block in result["blocks"]
        if block.get("type") == "section"
        and isinstance(block.get("text", {}).get("text"), str)
        and "Total Cost" in block["text"]["text"]
    ]

    assert len(total_cost_blocks) == 1
    assert ":moneybag: *Total Cost:* $26.25" in total_cost_blocks[0]["text"]["text"]


@patch("app.slack.templates.views.job_summary_string")
def test_verify_job_modal_single_language(mock_summary):
    mock_summary.return_value = "Test summary"

    job = {
        "uuid": "job1",
        "target_languages": [{"uuid": "lang1", "name": "Spanish", "selected": True}],
        "source_files": [
            {
                "report": {
                    "language_uuid": "source1",
                    "evaluation_reports": [{"target_language": "lang1"}],
                },
                "target_files": [{"language_uuid": "lang1"}],
            }
        ],
    }

    all_langs = [{"uuid": "source1", "name": "English"}]
    costs = [{"language_uuid": "lang1", "cost": "10.50"}]

    result = verify_job_modal(job, all_langs, costs)

    # Verify total cost section does not exist for single language
    total_cost_blocks = [
        block
        for block in result["blocks"]
        if block.get("type") == "section"
        and isinstance(block.get("text", {}).get("text"), str)
        and "Total Cost" in block["text"]["text"]
    ]

    assert len(total_cost_blocks) == 0
