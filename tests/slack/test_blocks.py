from unittest.mock import patch

from app.auth.connector import RayConnection, RaySuperGroup
from app.ray.events.models import (
    JobQuoteCreatedEvent,
    Language,
    QuoteInfo,
    QuoteLangPrice,
)
from app.slack.templates.blocks import (
    evaluate_success_blocks,
    home_auth_blocks,
    job_link_block,
    job_summary_no_score,
    job_summary_string,
    quote_message_block,
    verify_quote_blocks,
)


class TestHomeAuthBlocks:
    """Tests for home_auth_blocks function."""

    def test_home_auth_blocks_with_connected_client(self, ray_client, user_id, team_id):
        """Test home auth blocks when client is connected."""
        super_group = RaySuperGroup(
            id="sg-123",
            name="Test Group",
            slack_team_id=team_id,
            verify_organization_uuid="org-123",
            slack_enterprise_id=None,
        )
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)

        blocks = home_auth_blocks(user_id, team_id, None, "C123", ray_connection)

        assert len(blocks) == 2
        assert blocks[0]["type"] == "section"
        assert blocks[1]["type"] == "section"
        assert "Test Group" in blocks[0]["text"]["text"]

    def test_home_auth_blocks_with_sso_client(self, ray_client, user_id, team_id):
        """Test home auth blocks with SSO client."""
        super_group = RaySuperGroup(
            id="sg-123",
            name="Test Group",
            slack_team_id=team_id,
            verify_organization_uuid="org-123",
            slack_enterprise_id=None,
        )
        ray_client.sso = True
        ray_connection = RayConnection(super_group=[super_group], client=ray_client)

        blocks = home_auth_blocks(user_id, team_id, None, "C123", ray_connection)

        assert len(blocks) == 2
        assert "connected" in blocks[1]["text"]["text"].lower()

    def test_home_auth_blocks_ibm_enterprise(self, user_id, team_id):
        """Test home auth blocks for IBM enterprise."""
        with patch("app.slack.templates.blocks.is_ibm_enterprise", return_value=True):
            blocks = home_auth_blocks(user_id, team_id, "E123", "C123", None)

            assert len(blocks) == 2
            assert blocks[1]["type"] == "actions"
            assert len(blocks[1]["elements"]) == 1
            assert blocks[1]["elements"][0]["action_id"] == "login_sso"
            assert "Quality Evaluation" not in blocks[0]["text"]["text"]

    def test_home_auth_blocks_no_connection(self, user_id, team_id):
        """Test home auth blocks when not connected."""
        with patch("app.slack.templates.blocks.is_ibm_enterprise", return_value=False):
            blocks = home_auth_blocks(user_id, team_id, None, "C123", None)

            assert len(blocks) == 2
            assert blocks[1]["type"] == "actions"
            assert len(blocks[1]["elements"]) == 1
            assert blocks[1]["elements"][0]["action_id"] == "login"


class TestJobLinkBlock:
    """Tests for job_link_block function."""

    def test_job_link_block(self):
        """Test creating a job link block."""
        block = job_link_block("job-123", "client-123")

        assert block["type"] == "actions"
        assert len(block["elements"]) == 1
        assert block["elements"][0]["type"] == "button"
        assert block["elements"][0]["style"] == "primary"
        assert "job-123" in block["elements"][0]["url"]


class TestJobSummaryString:
    """Tests for job_summary_string function."""

    def test_job_summary_string_with_report(self):
        """Test job summary string with report."""
        source_lang = {"name": "English"}
        lang = {"name": "French", "report": {"score": 0.95}}
        file = {"filename": "test.txt"}

        result = job_summary_string(source_lang, lang, file)

        assert "English" in result
        assert "French" in result
        assert "test.txt" in result

    def test_job_summary_string_without_report(self):
        """Test job summary string without report."""
        source_lang = {"name": "English"}
        lang = {"name": "French"}
        file = {"filename": "test.txt"}

        result = job_summary_string(source_lang, lang, file)

        assert "English" in result
        assert "French" in result
        assert "test.txt" in result


class TestJobSummaryNoScore:
    """Tests for job_summary_no_score function."""

    def test_job_summary_no_score(self):
        """Test job summary without score."""
        lang = {"name": "French"}
        file = {"filename": "test.txt"}

        result = job_summary_no_score(lang, file)

        assert "French" in result
        assert "test.txt" in result


class TestVerifyQuoteBlocks:
    """Tests for verify_quote_blocks function."""

    def test_verify_quote_blocks_basic(self):
        """Test basic verify quote blocks."""
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

        blocks = verify_quote_blocks(job, costs)

        assert len(blocks) > 0
        assert any(block.get("block_id") == "total_cost_block" for block in blocks)
        assert any(
            block.get("block_id") == "total_estimated_time_block" for block in blocks
        )

    def test_verify_quote_blocks_with_submitted_status(self):
        """Test verify quote blocks with submitted human job status."""
        job = {
            "uuid": "job-123",
            "workflow_uuid": "workflow-123",
            "target_languages": [{"uuid": "lang-123", "name": "French"}],
            "source_files": [
                {
                    "file_uuid": "file-123",
                    "filename": "test.txt",
                    "target_files": [
                        {
                            "language_uuid": "lang-123",
                            "human_job_status": "Submitted",
                        }
                    ],
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

        blocks = verify_quote_blocks(job, costs, selectable=False)

        # Should have blocks but not include cost in total
        assert len(blocks) > 0

    def test_verify_quote_blocks_with_cancelled_status(self):
        """Test verify quote blocks with cancelled human job status."""
        job = {
            "uuid": "job-123",
            "workflow_uuid": "workflow-123",
            "target_languages": [{"uuid": "lang-123", "name": "French"}],
            "source_files": [
                {
                    "file_uuid": "file-123",
                    "filename": "test.txt",
                    "target_files": [
                        {
                            "language_uuid": "lang-123",
                            "human_job_status": "Cancelled",
                        }
                    ],
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

        blocks = verify_quote_blocks(job, costs)

        assert len(blocks) > 0
        assert any("cancelled" in str(block).lower() for block in blocks)

    def test_verify_quote_blocks_with_evaluation_report(self):
        """Test verify quote blocks with evaluation report."""
        # Note: workflow_uuid must NOT be HUMAN_EVALUATION_WORKFLOW_UUID
        # because the code skips reports when it matches
        job = {
            "uuid": "job-123",
            "workflow_uuid": "other-workflow-uuid",
            "target_languages": [{"uuid": "lang-123", "name": "French"}],
            "source_files": [
                {
                    "file_uuid": "file-123",
                    "filename": "test.txt",
                    "target_files": [],
                    "report": {
                        "language_uuid": "source-uuid",
                        "evaluation_reports": [
                            {
                                "target_language": "lang-123",
                                "count": {
                                    "bad": 1,
                                    "good": 5,
                                    "best": 2,
                                    "acceptable": 1,
                                    "translation_memory": 1,
                                },
                                "score": 0.85,
                            }
                        ],
                    },
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

        mock_languages = [
            {"uuid": "source-uuid", "name": "English", "code": "en"},
            {"uuid": "lang-123", "name": "French", "code": "fr"},
        ]
        with patch(
            "app.slack.templates.blocks.get_languages_sync", return_value=mock_languages
        ):
            blocks = verify_quote_blocks(job, costs, selectable=True)

            assert len(blocks) > 0
            # Should have summary and score blocks
            assert any("Summary" in str(block) for block in blocks)
            assert any("Overall Score" in str(block) for block in blocks)

    def test_verify_quote_blocks_not_selectable(self):
        """Test verify quote blocks when not selectable."""
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

        blocks = verify_quote_blocks(job, costs, selectable=False)

        assert len(blocks) > 0
        # Should not have checkboxes when not selectable
        assert not any(
            block.get("type") == "actions"
            and block.get("block_id", "").startswith("verification_checkbox")
            for block in blocks
        )


class TestEvaluateSuccessBlocks:
    """Tests for evaluate_success_blocks function."""

    def test_evaluate_success_blocks_basic(self):
        """Test basic evaluate success blocks."""
        job = {
            "target_languages": [{"uuid": "lang-123", "name": "French"}],
            "source_files": [
                {
                    "filename": "test.txt",
                    "target_files": [],
                    "report": {"language_uuid": "source-uuid"},
                }
            ],
        }

        with patch("app.slack.templates.blocks.get_languages_sync", return_value=[]):
            blocks = evaluate_success_blocks(job)

            assert len(blocks) > 0
            assert blocks[0]["type"] == "section"
            assert "test.txt" in blocks[0]["text"]["text"]

    def test_evaluate_success_blocks_with_submitted_status(self):
        """Test evaluate success blocks with submitted status."""
        job = {
            "target_languages": [{"uuid": "lang-123", "name": "French"}],
            "source_files": [
                {
                    "filename": "test.txt",
                    "target_files": [
                        {
                            "language_uuid": "lang-123",
                            "human_job_status": "Submitted",
                        }
                    ],
                    "report": {"language_uuid": "source-uuid"},
                }
            ],
        }

        with patch("app.slack.templates.blocks.get_languages_sync", return_value=[]):
            blocks = evaluate_success_blocks(job)

            assert len(blocks) > 0
            assert any("submitted" in str(block).lower() for block in blocks)

    def test_evaluate_success_blocks_with_evaluation_report(self):
        """Test evaluate success blocks with evaluation report."""
        job = {
            "target_languages": [{"uuid": "lang-123", "name": "French"}],
            "source_files": [
                {
                    "filename": "test.txt",
                    "target_files": [],
                    "report": {
                        "language_uuid": "source-uuid",
                        "evaluation_reports": [
                            {
                                "target_language": "lang-123",
                                "count": {
                                    "bad": 1,
                                    "good": 5,
                                    "best": 2,
                                    "acceptable": 1,
                                    "translation_memory": 1,
                                },
                                "score": 0.85,
                            }
                        ],
                    },
                }
            ],
        }

        with patch("app.slack.templates.blocks.get_languages_sync", return_value=[]):
            blocks = evaluate_success_blocks(job)

            assert len(blocks) > 0
            assert any("Summary" in str(block) for block in blocks)
            assert any("Overall Score" in str(block) for block in blocks)

    def test_evaluate_success_blocks_with_download_button(self):
        """Test evaluate success blocks includes download button."""
        job = {
            "target_languages": [{"uuid": "lang-123", "name": "French"}],
            "source_files": [
                {
                    "filename": "test.txt",
                    "target_files": [
                        {
                            "language_uuid": "lang-123",
                            "target_file_uuid": "target-file-123",
                        }
                    ],
                    "report": {"language_uuid": "source-uuid"},
                }
            ],
        }

        with patch("app.slack.templates.blocks.get_languages_sync", return_value=[]):
            blocks = evaluate_success_blocks(job)

            assert len(blocks) > 0
            # Should have download button
            assert any(
                block.get("type") == "actions"
                and any(
                    elem.get("action_id") == "download_ai_translation_action"
                    for elem in block.get("elements", [])
                )
                for block in blocks
            )


class TestQuoteMessageBlock:
    """Tests for quote_message_block function."""

    def test_quote_message_block_basic(self):
        """Test basic quote message block."""
        quote = JobQuoteCreatedEvent(
            uuid="job-123",
            id="123",
            client_id="client-123",
            status="LEAD",
            client_reference="REF-123",
            sl=Language(code="en", label="English"),
            tl=[Language(code="fr", label="French")],
            service="Human Translation",
            turnaround_days=5.0,
            quote=QuoteInfo(
                currency="USD",
                quote=100.0,
                quote_nett=100.0,
                quote_detail_url="https://example.com/detail",
                quote_accept_url="https://example.com/accept",
                quote_cancel_url="https://example.com/cancel",
                tl={"fr": QuoteLangPrice(price=50.0)},
            ),
        )

        blocks = quote_message_block(quote, "https://example.com/job", False)

        assert len(blocks) > 0
        assert blocks[0]["type"] == "section"
        assert "English" in str(blocks)
        assert "French" in str(blocks)

    def test_quote_message_block_with_tax(self):
        """Test quote message block with tax."""
        quote = JobQuoteCreatedEvent(
            uuid="job-123",
            id="123",
            client_id="client-123",
            status="LEAD",
            client_reference="REF-123",
            sl=Language(code="en", label="English"),
            tl=[],
            service="Human Translation",
            turnaround_days=5.0,
            quote=QuoteInfo(
                currency="USD",
                quote=110.0,
                quote_nett=100.0,
                quote_detail_url="https://example.com/detail",
                quote_accept_url="https://example.com/accept",
                quote_cancel_url="https://example.com/cancel",
                tl={},
            ),
        )

        blocks = quote_message_block(quote, "https://example.com/job", False)

        assert any("incl. tax" in str(block) for block in blocks)

    def test_quote_message_block_ibm_enterprise(self):
        """Test quote message block for IBM enterprise."""
        quote = JobQuoteCreatedEvent(
            uuid="job-123",
            id="123",
            client_id="client-123",
            status="LEAD",
            client_reference="REF-123",
            sl=Language(code="en", label="English"),
            tl=[],
            service="Human Translation",
            turnaround_days=5.0,
            quote=QuoteInfo(
                currency="USD",
                quote=100.0,
                quote_nett=100.0,
                quote_detail_url="https://example.com/detail",
                quote_accept_url="https://example.com/accept",
                quote_cancel_url="https://example.com/cancel",
                tl={},
            ),
        )

        blocks = quote_message_block(quote, "https://example.com/job", True)

        # Should not have "View in Verify" button for IBM
        assert not any("View in Verify" in str(block) for block in blocks)

    def test_quote_message_block_zero_turnaround(self):
        """Test quote message block with zero turnaround days."""
        quote = JobQuoteCreatedEvent(
            uuid="job-123",
            id="123",
            client_id="client-123",
            status="LEAD",
            client_reference="REF-123",
            sl=Language(code="en", label="English"),
            tl=[],
            service="Human Translation",
            turnaround_days=0.0,
            quote=QuoteInfo(
                currency="USD",
                quote=100.0,
                quote_nett=100.0,
                quote_detail_url="https://example.com/detail",
                quote_accept_url="https://example.com/accept",
                quote_cancel_url="https://example.com/cancel",
                tl={},
            ),
        )

        blocks = quote_message_block(quote, "https://example.com/job", False)

        assert len(blocks) > 0
