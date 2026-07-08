"""Tests for sequential evaluate quote Slack blocks and messages."""

from app.slack.pdf_evaluate_quotes import estimate_pdf_evaluate_ai_tokens
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
            job_uuid="job-1",
        )

        action_ids = [
            element["action_id"]
            for block in blocks
            if block.get("type") == "actions"
            for element in block.get("elements", [])
        ]
        assert "evaluation_ai_quote_accept" in action_ids
        rendered = str(blocks)
        assert "Token cost" not in rendered
        assert "Total tokens" not in rendered
        assert "US$2.40" in rendered
        assert "US$1.00" in rendered
        assert "US$3.40" in rendered

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
        assert "PDF conversion cost" in rendered
        assert "US$2.40" in rendered
        assert "US$1.00" in rendered
        assert "US$3.40" in rendered

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
        assert "PDF conversion cost" in rendered

    def test_pdf_evaluate_prequote_estimates_from_file_sizes_and_targets(self):
        tokens = estimate_pdf_evaluate_ai_tokens(
            [{"size": 1000}, {"size": 500}],
            target_language_count=2,
        )

        assert tokens == 6
