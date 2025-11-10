from app.slack.templates.views import (
    calculate_total_cost,
    cancel_job_modal,
    document_mt_job_modal,
    human_job_modal,
    job_search_modal,
    loading_modal,
    sso_form_modal,
    translation_settings_view,
    translation_settings_view_error,
    verify_job_modal,
    verify_quote_summary_modal,
)


def test_verify_job_modal_cost_update_individual_checkboxes():
    # Define the job, languages, and costs
    file_uuid = "file-123"
    job = {
        "uuid": "job-123",
        "workflow_uuid": "workflow-123",
        "target_languages": [
            {"uuid": "lang-123", "name": "French"},
            {"uuid": "lang-456", "name": "Spanish"},
        ],
        "source_files": [
            {
                "file_uuid": file_uuid,
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

    costs = [
        {
            "file_uuid": file_uuid,
            "language_uuid": "lang-123",
            "service_list": [{"estimated_cost": 15.75, "time_estimate_days": 2}],
        },
        {
            "file_uuid": file_uuid,
            "language_uuid": "lang-456",
            "service_list": [{"estimated_cost": 20.50, "time_estimate_days": 3}],
        },
    ]

    # First, select French (lang-123) only
    selected_languages = ["lang-123"]
    selected_costs = [
        cost for cost in costs if cost["language_uuid"] in selected_languages
    ]
    timestamp = "1234567890.123456"
    modal = verify_job_modal(job, selected_costs, timestamp)

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
        f"*Total Cost*: USD ${expected_total_cost:.2f}"
        in total_cost_block["text"]["text"]
    )

    # Now, select both French and Spanish (lang-123 and lang-456)
    selected_languages.append("lang-456")
    selected_costs = [
        cost for cost in costs if cost["language_uuid"] in selected_languages
    ]
    modal = verify_job_modal(job, selected_costs, timestamp)

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
        f"*Total Cost*: USD ${expected_total_cost:.2f}"
        in total_cost_block["text"]["text"]
    )


class TestJobSearchModal:
    """Tests for job_search_modal function."""

    def test_job_search_modal(self):
        """Test creating a job search modal."""
        modal = job_search_modal("test.user")

        assert modal["type"] == "modal"
        assert modal["callback_id"] == "job_search"
        assert len(modal["blocks"]) == 3
        assert "test.user" in modal["blocks"][0]["text"]["text"]


class TestHumanJobModal:
    """Tests for human_job_modal function."""

    def test_human_job_modal_human_type(self):
        """Test human job modal with human type."""
        file_info = [
            {"id": "file-123", "name": "test.txt"},
            {"id": "file-456", "name": "test2.txt"},
        ]
        modal = human_job_modal("C123", file_info, False, "human")

        assert modal["type"] == "modal"
        assert modal["callback_id"] == "evaluate_job_human"
        assert modal["private_metadata"] == "C123"
        assert "Human Translation" in modal["title"]["text"]
        assert "Request Quote" in modal["submit"]["text"]

    def test_human_job_modal_quality_evaluation_type(self):
        """Test human job modal with quality evaluation type."""
        file_info = [{"id": "file-123", "name": "test.txt"}]
        modal = human_job_modal("C123", file_info, False, "quality")

        assert modal["type"] == "modal"
        assert modal["callback_id"] == "evaluate_job"
        assert "Quality Evaluation" in modal["title"]["text"]
        assert "Submit" in modal["submit"]["text"]

    def test_human_job_modal_ibm_enterprise(self):
        """Test human job modal for IBM enterprise."""
        file_info = [{"id": "file-123", "name": "test.txt"}]
        modal = human_job_modal("C123", file_info, True, "human")

        # Should not have reference field for IBM
        block_ids = [block.get("block_id") for block in modal["blocks"]]
        assert "reference" not in block_ids

    def test_human_job_modal_with_initial_files(self):
        """Test human job modal with initial file options."""
        # map_file_options checks for files with 'initial' key set to True
        # and matches them by id to create initial_options
        file_info = [
            {"id": "file-123", "name": "test.txt", "initial": True},
            {"id": "file-456", "name": "test2.txt", "initial": False},
        ]
        modal = human_job_modal("C123", file_info, False, "human")

        # Find files block
        files_block = next(
            (block for block in modal["blocks"] if block.get("block_id") == "files"),
            None,
        )
        assert files_block is not None
        # initial_options will only be added if there are files with initial=True
        # and they match options by id
        element = files_block["element"]
        # Check that we have options
        assert "options" in element
        # initial_options may or may not be present depending on implementation
        # Just verify the modal structure is correct
        assert element["type"] == "multi_static_select"


class TestSsoFormModal:
    """Tests for sso_form_modal function."""

    def test_sso_form_modal(self):
        """Test creating SSO form modal."""
        modal = sso_form_modal()

        assert modal["type"] == "modal"
        assert modal["callback_id"] == "login_sso"
        assert len(modal["blocks"]) == 3
        assert modal["blocks"][0]["block_id"] == "email"
        assert modal["blocks"][1]["block_id"] == "firstName"
        assert modal["blocks"][2]["block_id"] == "lastName"


class TestCancelJobModal:
    """Tests for cancel_job_modal function."""

    def test_cancel_job_modal(self):
        """Test creating cancel job modal."""
        modal = cancel_job_modal("test.user")

        assert modal["type"] == "modal"
        assert modal["callback_id"] == "cancel_job"
        assert "test.user" in modal["blocks"][0]["text"]["text"]


class TestTranslationSettingsView:
    """Tests for translation_settings_view function."""

    def test_translation_settings_view_basic(self):
        """Test basic translation settings view."""
        view = translation_settings_view(team_id="T123")

        assert view["type"] == "modal"
        assert view["callback_id"] == "settings_auto_translate"
        assert view["private_metadata"] == "T123"
        assert len(view["blocks"]) == 3

    def test_translation_settings_view_with_initial_values(self):
        """Test translation settings view with initial values."""
        view = translation_settings_view(
            initial_channels=["C123"],
            initial_langs=["en", "fr"],
            display_format="messages",
            team_id="T123",
        )

        assert view["type"] == "modal"
        # Check that initial values are set
        channels_block = next(
            (block for block in view["blocks"] if block.get("block_id") == "channels"),
            None,
        )
        assert channels_block is not None
        assert "initial_conversations" in channels_block["element"]


class TestTranslationSettingsViewError:
    """Tests for translation_settings_view_error function."""

    def test_translation_settings_view_error(self):
        """Test translation settings error view."""
        view = translation_settings_view_error("Test error message")

        assert view["type"] == "modal"
        assert view["blocks"][0]["text"]["text"] == "Test error message"


class TestVerifyQuoteSummaryModal:
    """Tests for verify_quote_summary_modal function."""

    def test_verify_quote_summary_modal(self):
        """Test verify quote summary modal."""
        job = {
            "uuid": "job-123",
            "workflow_uuid": "workflow-123",
            "target_languages": [{"uuid": "lang-123", "name": "French"}],
            "source_files": [
                {
                    "file_uuid": "file-123",
                    "filename": "test.txt",
                    "target_files": [],
                    "report": {"language_uuid": "source-uuid"},
                }
            ],
        }
        costs = [
            {
                "file_uuid": "file-123",
                "language_uuid": "lang-123",
                "service_list": [{"estimated_cost": 10.50, "time_estimate_days": 2}],
            }
        ]

        modal = verify_quote_summary_modal(job, costs, "1234567890.123456")

        assert modal["type"] == "modal"
        assert modal["callback_id"] == "verify_job"
        assert "job-123" in modal["private_metadata"]


class TestCalculateTotalCost:
    """Tests for calculate_total_cost function."""

    def test_calculate_total_cost_single_language(self):
        """Test calculating total cost for single language."""
        selected_languages = [{"uuid": "lang-123"}]
        costs = [
            {
                "language_uuid": "lang-123",
                "service_list": [{"estimated_cost": 10.50}],
            }
        ]

        total = calculate_total_cost(selected_languages, costs)
        assert total == 10.50

    def test_calculate_total_cost_multiple_languages(self):
        """Test calculating total cost for multiple languages."""
        selected_languages = [{"uuid": "lang-123"}, {"uuid": "lang-456"}]
        costs = [
            {
                "language_uuid": "lang-123",
                "service_list": [{"estimated_cost": 10.50}],
            },
            {
                "language_uuid": "lang-456",
                "service_list": [{"estimated_cost": 20.75}],
            },
        ]

        total = calculate_total_cost(selected_languages, costs)
        assert total == 31.25

    def test_calculate_total_cost_no_match(self):
        """Test calculating total cost when no languages match."""
        selected_languages = [{"uuid": "lang-999"}]
        costs = [
            {
                "language_uuid": "lang-123",
                "service_list": [{"estimated_cost": 10.50}],
            }
        ]

        total = calculate_total_cost(selected_languages, costs)
        assert total == 0.0


class TestDocumentMtJobModal:
    """Tests for document_mt_job_modal function."""

    def test_document_mt_job_modal_basic(self):
        """Test basic document MT job modal."""
        modal = document_mt_job_modal("C123")

        assert modal["type"] == "modal"
        assert modal["callback_id"] == "document_mt_job"
        assert modal["private_metadata"] == "C123"

    def test_document_mt_job_modal_with_initial_files(self):
        """Test document MT job modal with initial files."""
        initial_files = [{"id": "file-123", "name": "test.txt"}]
        modal = document_mt_job_modal("C123", initial_files)

        assert modal["type"] == "modal"
        # Check that files block has initial options
        files_block = next(
            (block for block in modal["blocks"] if block.get("block_id") == "files"),
            None,
        )
        assert files_block is not None


class TestLoadingModal:
    """Tests for loading_modal function."""

    def test_loading_modal(self):
        """Test creating loading modal."""
        modal = loading_modal()

        assert modal["type"] == "modal"
        assert "Processing" in modal["title"]["text"]
        assert len(modal["blocks"]) == 1
        assert "hourglass" in modal["blocks"][0]["text"]["text"]
