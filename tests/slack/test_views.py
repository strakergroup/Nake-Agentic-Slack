import pytest
from app.slack.templates.views import verify_job_modal


def test_verify_job_modal_cost_update_individual_checkboxes():
    # Define the job, languages, and costs
    job = {
        "uuid": "job-123",
        "target_languages": [
            {"uuid": "lang-123", "name": "French"},
            {"uuid": "lang-456", "name": "Spanish"},
        ],
        "source_files": [
            {
                "filename": "example.txt",
                "report": {
                    "language_uuid": "source-uuid",
                    "evaluation_reports": [
                        {
                            "target_language": "lang-123",
                            "count": {
                                "bad": 0,
                                "good": 10,
                                "best": 0,
                                "acceptable": 0,
                                "translation_memory": 0,
                            },
                            "score": 85,
                        },
                        {
                            "target_language": "lang-456",
                            "count": {
                                "bad": 0,
                                "good": 20,
                                "best": 0,
                                "acceptable": 0,
                                "translation_memory": 0,
                            },
                            "score": 90,
                        },
                    ],
                },
                "target_files": [],
            }
        ],
    }

    all_langs = [
        {"uuid": "source-uuid", "name": "English"},
        {"uuid": "lang-123", "name": "French"},
        {"uuid": "lang-456", "name": "Spanish"},
    ]

    costs = [
        {"language_uuid": "lang-123", "service_list": [{"estimated_cost": 15.75}]},
        {"language_uuid": "lang-456", "service_list": [{"estimated_cost": 20.50}]},
    ]

    # First, select French (lang-123) only
    selected_languages = ["lang-123"]
    selected_costs = [
        cost for cost in costs if cost["language_uuid"] in selected_languages
    ]
    modal = verify_job_modal(job, all_langs, selected_costs)

    # Verify modal structure
    assert modal.get("type") == "modal"
    assert "blocks" in modal, "Modal is missing 'blocks' key"

    # Calculate expected total cost for French
    expected_total_cost = sum(
        service["estimated_cost"]
        for cost in selected_costs
        for service in cost["service_list"]
    )

    # Check if total_cost_block exists
    total_cost_block = next(
        (
            block
            for block in modal["blocks"]
            if block.get("block_id") == "total_cost_block"
        ),
        None,
    )

    assert (
        total_cost_block is not None
    ), "total_cost_block is missing from modal['blocks']"

    # Validate the total cost text dynamically
    assert (
        f"*Total Cost:* USD${expected_total_cost:.2f}"
        in total_cost_block["text"]["text"]
    )

    # Now, select both French and Spanish (lang-123 and lang-456)
    selected_languages.append("lang-456")
    selected_costs = [
        cost for cost in costs if cost["language_uuid"] in selected_languages
    ]
    modal = verify_job_modal(job, all_langs, selected_costs)

    # Calculate expected total cost for both French and Spanish
    expected_total_cost = sum(
        service["estimated_cost"]
        for cost in selected_costs
        for service in cost["service_list"]
    )

    # Check if total_cost_block exists again
    total_cost_block = next(
        (
            block
            for block in modal["blocks"]
            if block.get("block_id") == "total_cost_block"
        ),
        None,
    )

    assert (
        total_cost_block is not None
    ), "total_cost_block is missing from modal['blocks']"

    # Validate the updated total cost text dynamically
    assert (
        f"*Total Cost:* USD${expected_total_cost:.2f}"
        in total_cost_block["text"]["text"]
    )
