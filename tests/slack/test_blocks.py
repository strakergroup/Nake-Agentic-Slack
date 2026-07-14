from datetime import datetime
from unittest.mock import patch

from app.auth.connector import RayConnection, RaySuperGroup
from app.ray.events.models import (
    JobQuoteCreatedEvent,
    Language,
    QuoteInfo,
    QuoteLangPrice,
)
from app.slack.templates.blocks import (
    document_mt_quote_blocks,
    evaluate_ai_only_download_blocks,
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


class TestDocumentMtQuoteBlocks:
    """Tests for document_mt_quote_blocks function."""

    def _session(self, *, include_pdf: bool = False):
        session = {
            "quote_id": "quote-1",
            "enterprise_id": None,
            "quote": {
                "currency": "USD",
                "total_cost_usd": 10.00 if include_pdf else 1.25,
                "total_tokens": 500 if include_pdf else 125,
                "pdf_conversion_tokens": 100 if include_pdf else 0,
                "files": [
                    {
                        "file_id": "grid-1",
                        "file_name": "document.docx",
                        "character_count": 250000,
                        "pdf_conversion_page_count": None,
                        "pdf_conversion_tokens": 0,
                        "target_languages": [
                            {
                                "target_language": "fr",
                                "tokens": 500 if include_pdf else 125,
                                "cost_usd": 10.00 if include_pdf else 1.25,
                            }
                        ],
                    }
                ],
            },
        }
        if include_pdf:
            session["quote"]["files"].append(
                {
                    "file_id": "grid-2",
                    "file_name": "legal-appendix.pdf",
                    "character_count": 350000,
                    "pdf_conversion_page_count": 4,
                    "pdf_conversion_tokens": 100,
                    "target_languages": [
                        {
                            "target_language": "fr",
                            "tokens": 0,
                            "cost_usd": 0.0,
                        }
                    ],
                }
            )
        return session

    def test_document_mt_quote_blocks_use_service_quote_layout(self):
        with patch("app.slack.templates.blocks.is_ibm_enterprise", return_value=False):
            blocks = document_mt_quote_blocks(
                self._session(include_pdf=True),
                actions=False,
            )

        rendered = str(blocks)
        assert "Service Quote" in rendered
        assert "AI Translation" in rendered
        assert "PDF conversion" in rendered
        assert "PDF conversion cost" not in rendered
        assert rendered.index("PDF conversion") < rendered.index("AI Translation")
        assert "US$8.00" in rendered
        assert "US$2.00" in rendered
        assert "US$10.00" in rendered
        assert "estimated" not in rendered.lower()
        assert "Total AI Tokens" not in rendered

    def test_document_mt_quote_blocks_hide_pdf_section_without_pdf(self):
        with patch("app.slack.templates.blocks.is_ibm_enterprise", return_value=False):
            blocks = document_mt_quote_blocks(self._session(), actions=False)

        rendered = str(blocks)
        assert "PDF conversion" not in rendered
        assert "US$2.50" in rendered

    def test_document_mt_quote_blocks_show_accept_action(self):
        blocks = document_mt_quote_blocks(self._session(), actions=True)

        action_ids = [
            element["action_id"]
            for block in blocks
            if block.get("type") == "actions"
            for element in block.get("elements", [])
        ]
        assert action_ids == ["document_mt_quote_accept"]


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
        assert any(">Cancelled" in str(block) for block in blocks)
        assert "USD$" not in str(blocks)

    def test_verify_quote_blocks_selectable_hides_out_of_scope_languages(self):
        """Adjust Request omits file/language pairs with no active cost row."""
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
                    "target_files": [{"language_uuid": "lang-fr"}],
                    "report": {"language_uuid": "source-uuid"},
                },
                {
                    "file_uuid": "file-b",
                    "filename": "b.docx",
                    "target_files": [{"language_uuid": "lang-de"}],
                    "report": {"language_uuid": "source-uuid"},
                },
            ],
        }
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

        blocks = verify_quote_blocks(job, costs, selectable=True)
        rendered = str(blocks)

        assert "file-a:lang-fr:" in rendered
        assert "file-b:lang-de:" in rendered
        assert "file-a:lang-de:" not in rendered
        assert "file-b:lang-fr:" not in rendered
        assert "USD$0.00" not in rendered

    def test_verify_quote_blocks_post_qe_shows_pricing_for_submitted_target(self):
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
                "service_list": [
                    {
                        "estimated_cost": 80.0,
                        "time_estimate_days": 2,
                        "quality_discount": {
                            "tier": "good",
                            "word_discount_rate": 0.3,
                            "savings": 12.0,
                            "pricing_cap_applied": False,
                        },
                    }
                ],
            }
        ]

        blocks = verify_quote_blocks(
            job,
            costs,
            selectable=False,
            additional_costs=[
                {
                    "label": "Quality Evaluation",
                    "cost": 0.08,
                    "file_uuid": "file-123",
                    "language_uuid": "lang-123",
                }
            ],
            show_quality_discount=True,
            show_savings=True,
            embed_additional_costs_in_line_price=True,
            total_cost_label="Final Cost",
        )
        rendered = str(blocks)

        assert "USD$80.08" in rendered
        assert "Quality: good" in rendered
        assert "-30% off" not in rendered
        assert "submitted for this language" not in rendered
        assert "Final Cost*: USD $80.08" in rendered

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

    def test_verify_quote_blocks_shows_quality_discount_when_not_selectable(self):
        """Test human quote message includes QE discount metadata."""
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
                "service_list": [
                    {
                        "estimated_cost": 10.50,
                        "time_estimate_days": 2,
                        "quality_discount": {
                            "tier": "best",
                            "word_discount_rate": 0.5,
                            "savings": 10.5,
                            "pricing_cap_applied": False,
                        },
                    }
                ],
            }
        ]

        blocks = verify_quote_blocks(job, costs, selectable=False)
        rendered = str(blocks)

        assert "USD$10.50" in rendered
        assert "Quality: best" in rendered
        assert "-50% off" not in rendered
        assert "Total Cost*: USD $10.50" in rendered
        assert "saved $10.50" in rendered

    def test_verify_quote_blocks_hides_zero_quality_discount(self):
        """Test zero discount metadata is not shown."""
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
                "service_list": [
                    {
                        "estimated_cost": 10.50,
                        "time_estimate_days": 2,
                        "quality_discount": {
                            "tier": "unscored",
                            "word_discount_rate": 0,
                            "savings": 0,
                            "pricing_cap_applied": False,
                        },
                    }
                ],
            }
        ]

        blocks = verify_quote_blocks(job, costs, selectable=False)

        assert "Quality:" not in str(blocks)

    def test_verify_quote_blocks_shows_estimated_quality_discount_and_qe_cost(self):
        """Combined QE + HT quotes include QE cost and estimated worst-case discount."""
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
                "service_list": [
                    {
                        "estimated_cost": 90.00,
                        "time_estimate_days": 2,
                        "quality_discount": {
                            "tier": "bad",
                            "word_discount_rate": 0.1,
                            "savings": 10.0,
                            "pricing_cap_applied": False,
                            "is_estimate": True,
                        },
                    }
                ],
            }
        ]

        blocks = verify_quote_blocks(
            job,
            costs,
            selectable=False,
            additional_costs=[
                {
                    "label": "Quality Evaluation",
                    "cost": 1.60,
                    "file_uuid": "file-123",
                    "language_uuid": "lang-123",
                }
            ],
            show_quality_discount=False,
            show_savings=False,
            embed_additional_costs_in_line_price=True,
        )
        rendered = str(blocks)

        assert "Worst-case QE discount: -10% off" not in rendered
        assert "Quality:" not in rendered
        assert "*Quality Evaluation*: USD $1.60" not in rendered
        assert "Quality Evaluation: USD" not in rendered
        assert "USD$91.60" in rendered
        assert "Maximum Total Cost*: USD $91.60" in rendered
        assert "saved $10.00" not in rendered

    def test_verify_quote_blocks_can_hide_quality_and_savings(self):
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
                "service_list": [
                    {
                        "estimated_cost": 10.50,
                        "time_estimate_days": 2,
                        "quality_discount": {
                            "tier": "good",
                            "word_discount_rate": 0.3,
                            "savings": 4.5,
                            "pricing_cap_applied": False,
                        },
                    }
                ],
            }
        ]

        blocks = verify_quote_blocks(
            job,
            costs,
            selectable=False,
            show_quality_discount=False,
            show_savings=False,
        )
        rendered = str(blocks)

        assert "Quality:" not in rendered
        assert "saved $" not in rendered
        assert "Maximum Total Cost*: USD $10.50" in rendered

    def test_verify_quote_blocks_distributes_qe_cost_by_target(self):
        """Target-scoped QE costs can be embedded in each file/language quote row."""
        job = {
            "uuid": "job-123",
            "workflow_uuid": "workflow-123",
            "target_languages": [
                {"uuid": "lang-fr", "name": "French"},
                {"uuid": "lang-es", "name": "Spanish"},
            ],
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
                "language_uuid": "lang-fr",
                "service_list": [{"estimated_cost": 90.00, "time_estimate_days": 2}],
            },
            {
                "file_uuid": "file-123",
                "language_uuid": "lang-es",
                "service_list": [{"estimated_cost": 80.00, "time_estimate_days": 2}],
            },
        ]

        blocks = verify_quote_blocks(
            job,
            costs,
            selectable=False,
            additional_costs=[
                {
                    "label": "Quality Evaluation",
                    "cost": 0.80,
                    "file_uuid": "file-123",
                    "language_uuid": "lang-fr",
                },
                {
                    "label": "Quality Evaluation",
                    "cost": 0.80,
                    "file_uuid": "file-123",
                    "language_uuid": "lang-es",
                },
            ],
            embed_additional_costs_in_line_price=True,
        )
        rendered = str(blocks)

        assert rendered.count("Quality Evaluation: USD $0.80") == 0
        assert "USD$90.80" in rendered
        assert "USD$80.80" in rendered
        assert "*Quality Evaluation*: USD $1.60" not in rendered
        assert "Total Cost*: USD $171.60" in rendered

    def test_verify_quote_blocks_shows_quality_discount_in_selectable_label(self):
        """Test adjust-request checkbox includes QE discount metadata."""
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
                "service_list": [
                    {
                        "estimated_cost": 10.50,
                        "time_estimate_days": 2,
                        "quality_discount": {
                            "tier": "good",
                            "word_discount_rate": 0.3,
                            "savings": 4,
                            "pricing_cap_applied": True,
                        },
                    }
                ],
            }
        ]

        blocks = verify_quote_blocks(job, costs, selectable=True)
        rendered = str(blocks)

        assert "USD$10.50" in rendered
        assert "saved $4.00" not in rendered
        assert "Quality: good" in rendered
        assert "-30% off" not in rendered
        assert "file-123:lang-123:2:0.00:0.00" in rendered

    def test_verify_quote_blocks_adds_target_qe_cost_to_selectable_value(self):
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

        blocks = verify_quote_blocks(
            job,
            costs,
            selectable=True,
            additional_costs=[
                {
                    "label": "Quality Evaluation",
                    "cost": 0.80,
                    "file_uuid": "file-123",
                    "language_uuid": "lang-123",
                }
            ],
        )
        rendered = str(blocks)

        assert "Quality Evaluation: USD $0.80" in rendered
        assert "file-123:lang-123:2:0.00:0.80" in rendered

    def test_verify_quote_blocks_embeds_qe_cost_in_selectable_value(self):
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

        blocks = verify_quote_blocks(
            job,
            costs,
            selectable=True,
            additional_costs=[
                {
                    "label": "Quality Evaluation",
                    "cost": 0.80,
                    "file_uuid": "file-123",
                    "language_uuid": "lang-123",
                }
            ],
            embed_additional_costs_in_line_price=True,
        )
        rendered = str(blocks)

        assert "Quality Evaluation: USD $0.80" not in rendered
        assert "USD$11.30" in rendered
        assert "file-123:lang-123:2:0.00:0.00" in rendered

    @patch("app.slack.templates.blocks.datetime")
    def test_verify_quote_blocks_uses_max_turnaround_across_targets(
        self, mock_datetime
    ):
        """Estimated completion uses global max (Verify-aligned), not max × count."""
        mock_datetime.now.return_value = datetime(2026, 6, 26)
        mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        job = {
            "uuid": "job-123",
            "workflow_uuid": "workflow-123",
            "target_languages": [
                {"uuid": f"lang-{i}", "name": f"Lang {i}"} for i in range(14)
            ],
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
                "language_uuid": f"lang-{i}",
                "service_list": [
                    {"estimated_cost": 10.50, "time_estimate_days": 2 if i else 5}
                ],
            }
            for i in range(14)
        ]

        blocks = verify_quote_blocks(job, costs, selectable=False)
        time_block = next(
            block
            for block in blocks
            if block.get("block_id") == "total_estimated_time_block"
        )

        # Max is 5 days (slowest lang), not 5 × 14 = 70
        assert "01 July 2026" in time_block["text"]["text"]


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

    def test_evaluate_ai_only_blocks_do_not_include_bulk_download_button(self):
        """AI-only empty states do not render download actions."""
        job = {
            "uuid": "job-123",
            "target_languages": [],
            "source_files": [],
        }

        with patch("app.slack.templates.blocks.get_languages_sync", return_value=[]):
            blocks = evaluate_ai_only_download_blocks(job)

        assert not any(block.get("type") == "actions" for block in blocks)


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
