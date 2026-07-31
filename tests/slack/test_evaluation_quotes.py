"""Tests for sequential evaluate quote Slack blocks and messages."""

from app.slack.templates.blocks import evaluation_credits_quote_blocks
from app.slack.templates.messages import EvaluationCreditsQuoteMessage


class TestEvaluationCreditsQuoteBlocks:
    def test_ai_translation_quote_blocks(self):
        blocks = evaluation_credits_quote_blocks(
            "AI Translation",
            120,
            pdf_page_count=2,
            pdf_tokens=50,
            accept_action_id="evaluation_ai_quote_accept",
            adjust_action_id="evaluation_ai_quote_adjust",
            job_uuid="job-1",
            language_costs=[
                {
                    "file_label": "source.docx",
                    "value": "lang-1",
                    "label": "French",
                    "token": 40,
                },
                {
                    "file_label": "source.docx",
                    "value": "lang-2",
                    "label": "German",
                    "token": 80,
                },
            ],
        )

        action_ids = [
            element["action_id"]
            for block in blocks
            if block.get("type") == "actions"
            for element in block.get("elements", [])
        ]
        assert "evaluation_ai_quote_accept" in action_ids
        assert "evaluation_ai_quote_adjust" in action_ids
        rendered = str(blocks)
        assert "Token cost" not in rendered
        assert "Total tokens" not in rendered
        assert "USD 1.00" in rendered
        assert "USD 3.40" in rendered
        assert "*AI Translation:*" in rendered
        assert rendered.index("PDF conversion") < rendered.index("AI Translation")
        assert rendered.index("AI Translation") < rendered.index(
            ":paperclip: *source.docx*"
        )
        assert ":paperclip: *source.docx*" in rendered
        assert "*French*\\n>USD 0.80" in rendered
        assert "*German*\\n>USD 1.60" in rendered
        assert (
            "AI pre-translation before human review will incur the following cost:"
            in rendered
        )
        assert "Review the quote below" not in rendered
        assert "Adjust Request" in rendered
        assert "remove languages and/or source files" in rendered
        assert "Estimated Completion" not in rendered
        assert "Due" not in rendered

    def test_ai_translation_quote_blocks_show_cancelled_rows(self):
        blocks = evaluation_credits_quote_blocks(
            "AI Translation",
            40,
            accept_action_id="evaluation_ai_quote_accept",
            job_uuid="job-1",
            actions=False,
            language_costs=[
                {
                    "file_label": "source.docx",
                    "value": "lang-1",
                    "label": "French",
                    "token": 40,
                },
                {
                    "file_label": "source.docx",
                    "value": "lang-2",
                    "label": "German",
                    "token": 80,
                    "cancelled": True,
                },
            ],
        )
        rendered = str(blocks)
        assert "*French*\\n>USD 0.80" in rendered
        assert "*German*\\n>AI Translate quote cancelled" in rendered
        assert ">Cancelled" not in rendered
        assert "*Total cost:* USD 0.80" in rendered
        assert "Estimated Completion" not in rendered

    def test_ai_translation_quote_blocks_distribute_minimum_charge(self):
        """Per-row ceils must not make language lines exceed the aggregate Total."""
        blocks = evaluation_credits_quote_blocks(
            "AI Translation",
            1,
            pdf_page_count=1,
            pdf_tokens=25,
            accept_action_id="evaluation_ai_quote_accept",
            job_uuid="job-1",
            actions=False,
            language_costs=[
                {
                    "file_label": "brief.pdf",
                    "value": "hr",
                    "label": "Croatian",
                    "token": 1,
                },
                {
                    "file_label": "brief.pdf",
                    "value": "ny",
                    "label": "Chichewa",
                    "token": 1,
                },
            ],
        )
        rendered = str(blocks)
        assert rendered.count("USD 0.01") == 2
        # PDF $0.50 + distributed AI $0.02; lines must not each show the minimum.
        assert "*Total cost:* USD 0.52" in rendered
        assert "USD 0.50" in rendered
        assert "USD 0.54" not in rendered
        assert "*Croatian*\\n>USD 0.02" not in rendered
        assert "*Chichewa*\\n>USD 0.02" not in rendered

    def test_evaluation_quote_blocks_display_dollar_cost(self):
        blocks = evaluation_credits_quote_blocks(
            "AI Translation",
            120,
            pdf_page_count=2,
            pdf_tokens=50,
            accept_action_id="evaluation_ai_quote_accept",
            job_uuid="job-1",
            is_ibm=True,
        )

        rendered = str(blocks)
        assert "Token cost" not in rendered
        assert "Total tokens" not in rendered
        assert "Cost" in rendered
        assert "PDF conversion" in rendered
        assert "PDF conversion cost" not in rendered
        assert rendered.index("PDF conversion") < rendered.index("AI Translation")
        assert "USD 2.40" in rendered
        assert "USD 1.00" in rendered
        assert "USD 3.40" in rendered

    def test_evaluation_credits_quote_message(self):
        message = EvaluationCreditsQuoteMessage(
            service_label="AI Translation",
            token_cost=80,
            job_uuid="job-2",
            accept_action_id="evaluation_ai_quote_accept",
        )

        assert message.text == "Service Quote"
        assert message.blocks
        action_ids = [
            element["action_id"]
            for block in message.blocks
            if block.get("type") == "actions"
            for element in block.get("elements", [])
        ]
        assert action_ids == ["evaluation_ai_quote_accept"]

    def test_evaluation_credits_quote_message_without_actions(self):
        message = EvaluationCreditsQuoteMessage(
            service_label="AI Translation",
            token_cost=3,
            job_uuid="job-3",
            accept_action_id="evaluation_ai_quote_accept",
            actions=False,
            status_message="Quote accepted.",
        )

        assert not any(block.get("type") == "actions" for block in message.blocks)
        assert any("Quote accepted." in str(block) for block in message.blocks)

    def test_pdf_evaluate_prequote_uses_ai_translation_label(self):
        message = EvaluationCreditsQuoteMessage(
            service_label="AI Translation",
            token_cost=80,
            job_uuid="quote-1",
            accept_action_id="evaluation_pdf_prequote_accept",
            pdf_page_count=2,
            pdf_tokens=50,
        )
        rendered = str(message.blocks)
        assert "Estimated" not in rendered
        assert "AI Translation" in rendered
        assert "PDF conversion" in rendered
        assert "PDF conversion cost" not in rendered
        assert rendered.index("PDF conversion") < rendered.index("AI Translation")


def test_human_job_quote_message_shows_accept_helper_on_pre_qe_estimate():
    from app.slack.templates.messages import HumanJobQuoteMessage

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
    message = HumanJobQuoteMessage(
        job,
        costs,
        actions=True,
        show_savings=False,
        show_quality_discount=False,
        show_accept_discount_helper=True,
    )
    rendered = str(message.blocks)
    assert "Maximum Total Cost" in rendered
    assert "Click *Accept Quote* to send your translation for human review" in rendered
    assert (
        "A *discount* will be applied to the quote above based on the quality of "
        "the AI translation."
    ) in rendered


def test_human_job_quote_message_hides_accept_helper_after_accept():
    from app.slack.templates.messages import HumanJobQuoteMessage

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
    message = HumanJobQuoteMessage(
        job,
        costs,
        actions=False,
        status_message=(
            "Quote accepted! Submitting for human translation and "
            "calculating your final discount based on AI quality..."
        ),
        show_savings=False,
        show_quality_discount=False,
    )
    rendered = str(message.blocks)
    assert (
        "Click *Accept Quote* to send your translation for human review" not in rendered
    )
    assert (
        "Click Accept Quote to send your translation for human review" not in rendered
    )
    assert "calculating your final discount based on AI quality" in rendered


def test_standalone_ht_quote_matches_prod_totals_without_discount_details():
    """Non-admin HT quotes render like the fixed HUMAN_EVALUATION workflow on prod."""
    from app.slack.evaluation_combined_quotes import standalone_ht_quote_message

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

    rendered = str(standalone_ht_quote_message(job, costs).blocks)

    assert "USD 10.50" in rendered
    assert "*Total Cost*: USD 10.50" in rendered
    assert "Quality: " not in rendered
    assert "saved USD" not in rendered
    assert "Maximum Total Cost" not in rendered
    # Admin pre-QE helper must not appear — prices are already final.
    assert "discount* will be applied" not in rendered
    assert "Click *Accept Quote* to send your translation" not in rendered
