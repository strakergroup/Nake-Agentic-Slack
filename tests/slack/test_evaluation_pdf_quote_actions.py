"""Tests for PDF evaluate quote accept Slack status copy."""

from app.slack.evaluation_pdf_quote_actions import pdf_evaluate_accept_status_message

CONVERTING_PDF_STATUS = "Quote accepted. Converting PDF and running AI translation..."
AI_ONLY_STATUS = "Quote accepted. Running AI translation..."


def test_pdf_evaluate_accept_status_omits_converting_pdf_when_all_pdfs_deselected():
    message = pdf_evaluate_accept_status_message(
        [{"id": "F2", "title": "Test.docx"}],
    )

    assert message == AI_ONLY_STATUS


def test_pdf_evaluate_accept_status_mentions_converting_pdf_when_a_pdf_remains():
    message = pdf_evaluate_accept_status_message(
        [
            {"id": "F1", "title": "1Test.pdf"},
            {"id": "F2", "title": "Test.docx"},
        ],
    )

    assert message == CONVERTING_PDF_STATUS


def test_pdf_evaluate_accept_status_mentions_converting_pdf_for_truncated_pdf_title():
    message = pdf_evaluate_accept_status_message(
        [{"id": "F1", "title": "A great summer vacation", "pdf_page_count": 2}],
    )

    assert message == CONVERTING_PDF_STATUS
