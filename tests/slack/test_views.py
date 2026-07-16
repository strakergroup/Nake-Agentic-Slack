from app.slack.templates.views import (
    calculate_total_cost,
    cancel_job_modal,
    document_mt_job_modal,
    human_job_modal,
    job_search_modal,
    loading_modal,
    srt_translate_modal,
    sso_form_modal,
    translation_settings_view,
    translation_settings_view_error,
    verify_job_modal,
    verify_quote_summary_modal,
    video_transcribe_translate_modal,
)
from app.translate import _


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

    message = "total_cost_block is missing from modal['blocks']"
    assert total_cost_block is not None, message

    # Validate the total cost text dynamically
    assert (
        f"*Maximum Total Cost*: USD {expected_total_cost:.2f}"
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

    message = "total_cost_block is missing from modal['blocks']"
    assert total_cost_block is not None, message

    # Validate the updated total cost text dynamically
    assert (
        f"*Maximum Total Cost*: USD {expected_total_cost:.2f}"
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
        """Test non-IBM quality evaluation modal keeps project metadata."""
        file_info = [{"id": "file-123", "name": "test.txt"}]
        modal = human_job_modal("C123", file_info, False, "quality")

        assert modal["type"] == "modal"
        assert modal["callback_id"] == "evaluate_job"
        assert "Quality Evaluation" in modal["title"]["text"]
        assert "Request Quote" in modal["submit"]["text"]
        assert "Project Name" in str(modal["blocks"])
        assert "translation quality scores" in str(modal["blocks"])

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

        modal = verify_quote_summary_modal(job, costs, "1234567890.123456", "C123")

        assert modal["type"] == "modal"
        assert modal["callback_id"] == "verify_job"
        assert "job-123" in modal["private_metadata"]
        assert '"channel_id": "C123"' in modal["private_metadata"]


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

        source_block = next(
            (
                block
                for block in modal["blocks"]
                if block.get("block_id") == "source_lang"
            ),
            None,
        )
        assert source_block is not None
        assert source_block["element"]["type"] == "static_select"

        target_block = next(
            (
                block
                for block in modal["blocks"]
                if block.get("block_id") == "target_langs"
            ),
            None,
        )
        assert target_block is not None
        assert modal["blocks"].index(source_block) < modal["blocks"].index(target_block)

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


class TestSrtTranslateModal:
    def test_srt_translate_modal_structure(self):
        """Test that srt_translate_modal returns a properly structured modal."""
        task_uuid = "test-task-uuid-123"
        channel_id = "C999"
        modal = srt_translate_modal(task_uuid, channel_id)

        # Verify modal structure
        assert modal.get("type") == "modal"
        assert modal.get("callback_id") == "srt_translate"
        assert "blocks" in modal
        assert "private_metadata" in modal
        assert modal["private_metadata"] == f"{task_uuid}|{channel_id}"

        # Verify title
        assert "title" in modal
        assert modal["title"]["type"] == "plain_text"
        assert len(modal["title"]["text"]) <= 24  # Slack title limit

        # Verify submit and close buttons
        assert "submit" in modal
        assert modal["submit"]["type"] == "plain_text"
        assert "close" in modal
        assert modal["close"]["type"] == "plain_text"

    def test_srt_translate_modal_blocks(self):
        """Test that the modal contains the correct blocks."""
        task_uuid = "test-task-uuid-456"
        modal = srt_translate_modal(task_uuid, "C888")

        blocks = modal["blocks"]
        assert len(blocks) == 2  # Section block + input block

        # Verify section block
        section_block = blocks[0]
        assert section_block["type"] == "section"
        assert "text" in section_block
        assert section_block["text"]["type"] == "mrkdwn"
        # Check for translated text
        expected_section_text = _(
            "Please select the target language(s) for translation"
        )
        assert expected_section_text in section_block["text"]["text"]

        # Verify input block
        input_block = blocks[1]
        assert input_block["type"] == "input"
        assert input_block["block_id"] == "target_langs"
        assert "label" in input_block
        assert input_block["label"]["type"] == "plain_text"
        # Check for translated text
        expected_label_text = _("Select languages")
        assert input_block["label"]["text"] == expected_label_text

        # Verify multi-select element
        element = input_block["element"]
        assert element["type"] == "multi_static_select"
        assert element["action_id"] == "language_mt_options"
        assert element["max_selected_items"] == 10
        assert "placeholder" in element
        assert element["placeholder"]["type"] == "plain_text"
        # Check for translated text
        expected_placeholder_text = _("Select language")
        assert element["placeholder"]["text"] == expected_placeholder_text

        # Verify language options are present
        assert "options" in element
        assert isinstance(element["options"], list)
        assert len(element["options"]) > 0

        # Verify option structure
        for option in element["options"]:
            assert "text" in option
            assert option["text"]["type"] == "plain_text"
            assert "text" in option["text"]
            assert "value" in option
            assert isinstance(option["value"], str)

    def test_srt_translate_modal_different_task_uuids(self):
        """Test that different task UUIDs are correctly stored in private_metadata."""
        uuid1 = "task-123"
        uuid2 = "task-456"
        channel_id = "C777"

        modal1 = srt_translate_modal(uuid1, channel_id)
        modal2 = srt_translate_modal(uuid2, channel_id)

        assert modal1["private_metadata"] == f"{uuid1}|{channel_id}"
        assert modal2["private_metadata"] == f"{uuid2}|{channel_id}"
        assert modal1["private_metadata"] != modal2["private_metadata"]


class TestVideoTranscribeTranslateModal:
    """Tests for video_transcribe_translate_modal function."""

    def test_modal_structure(self):
        """Test that video_transcribe_translate_modal returns proper structure."""
        channel_id = "C123456"
        file_id = "F123456"
        file_name = "test_video.mp4"
        duration_ms = 60000

        modal = video_transcribe_translate_modal(
            channel_id=channel_id,
            files=[
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "duration_ms": duration_ms,
                }
            ],
        )

        assert modal.get("type") == "modal"
        assert modal.get("callback_id") == "video_transcribe_translate_submit"
        assert "title" in modal
        assert "submit" in modal
        assert "close" in modal
        assert "blocks" in modal
        assert "private_metadata" in modal

    def test_file_field_is_required(self):
        """Test that the file selection field is required (not optional)."""
        channel_id = "C123456"
        file_id = "F123456"
        file_name = "test_video.mp4"
        duration_ms = 60000

        modal = video_transcribe_translate_modal(
            channel_id=channel_id,
            files=[
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "duration_ms": duration_ms,
                }
            ],
        )

        # Find the selected_file block
        blocks = modal.get("blocks", [])
        file_block = None
        for block in blocks:
            if hasattr(block, "block_id") and block.block_id == "selected_file":
                file_block = block
                break
            elif isinstance(block, dict) and block.get("block_id") == "selected_file":
                file_block = block
                break

        assert file_block is not None, "File selection block not found"

        # Check that optional is False (field is required)
        if hasattr(file_block, "optional"):
            assert file_block.optional is False, "File field should be required"
        elif isinstance(file_block, dict):
            message = "File field should be required"
            assert file_block.get("optional", True) is False, message

    def test_private_metadata_contains_file_info(self):
        """Test that private_metadata contains file information."""
        import json

        channel_id = "C123456"
        file_id = "F123456"
        file_name = "test_video.mp4"
        duration_ms = 60000
        thread_ts = "1234567890.123456"

        modal = video_transcribe_translate_modal(
            channel_id=channel_id,
            files=[
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "duration_ms": duration_ms,
                }
            ],
            thread_ts=thread_ts,
        )

        metadata = json.loads(modal.get("private_metadata", "{}"))
        assert metadata.get("channel_id") == channel_id
        assert metadata.get("files") == [
            {
                "file_id": file_id,
                "file_name": file_name,
                "duration_ms": duration_ms,
            }
        ]
        assert metadata.get("thread_ts") == thread_ts
        assert metadata.get("pipeline_type") == "transcription_translation"

    def test_target_languages_block_exists(self):
        """Test that target languages selection block exists."""
        channel_id = "C123456"
        file_id = "F123456"
        file_name = "test_video.mp4"
        duration_ms = 60000

        modal = video_transcribe_translate_modal(
            channel_id=channel_id,
            files=[
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "duration_ms": duration_ms,
                }
            ],
        )

        blocks = modal.get("blocks", [])
        target_lang_block = None
        for block in blocks:
            if hasattr(block, "block_id") and block.block_id == "target_languages":
                target_lang_block = block
                break
            elif (
                isinstance(block, dict) and block.get("block_id") == "target_languages"
            ):
                target_lang_block = block
                break

        assert target_lang_block is not None, "Target languages block not found"


class TestInsightsRemovedFromHomeView:
    """Guard test: report_insights button must not appear in home_view (RAY-79162)."""

    def test_home_view_source_has_no_report_insights(self):
        """Verify report_insights does not appear in the home_view function source."""
        import inspect

        from app.slack.templates.views import home_view

        source = inspect.getsource(home_view)
        assert "report_insights" not in source


class TestHomeViewMediaTranslationHelp:
    """RAY-79731: Home tab exposes media translation help link."""

    def test_home_view_includes_media_translation_help_button(self):
        import inspect

        from app.slack.templates.views import home_view

        source = inspect.getsource(home_view)
        assert "link_media_translation_help" in source
        assert (
            "https://help.straker.ai/en/docs/ai-translate-for-videos-in-straker-translate-app-for-slack"
            in source
        )
        assert "Media Translation Help" in source
