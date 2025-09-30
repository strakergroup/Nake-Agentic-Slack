from PyPDF2 import PdfWriter

from app.ray.file_validators import validate_google_slides_pdf, validate_json


def test_validate_json_valid_string():
    """Test validation of valid JSON string."""
    is_valid, message = validate_json('{"key": "value"}')
    assert is_valid is True
    assert message == ""


def test_validate_json_valid_bytes():
    """Test validation of valid JSON bytes."""
    is_valid, message = validate_json(b'{"key": "value"}')
    assert is_valid is True
    assert message == ""


def test_validate_json_invalid_structure():
    """Test validation of invalid JSON structure."""
    is_valid, message = validate_json("invalid json")
    assert is_valid is False
    assert "Invalid JSON. Please fix the issue and resubmit the file." in message


def test_validate_json_invalid_bytes():
    """Test validation with non-UTF8 bytes."""
    is_valid, message = validate_json(b"\x80\x81")
    assert is_valid is False
    assert message == "Invalid JSON: File must be UTF-8 encoded."


def test_validate_json_complex():
    """Test validation of complex JSON."""
    complex_json = """
    {
        "name": "test",
        "numbers": [1, 2, 3],
        "nested": {"a": 1}
    }
    """
    is_valid, message = validate_json(complex_json)
    assert is_valid is True
    assert message == ""


def _create_pdf(tmp_path, metadata=None):
    pdf_path = tmp_path / "sample.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    if metadata:
        writer.add_metadata(metadata)
    with pdf_path.open("wb") as f:
        writer.write(f)
    return pdf_path


def test_validate_google_slides_pdf_valid(tmp_path):
    pdf_path = _create_pdf(tmp_path, {"/Producer": "Acme PDF Engine"})

    is_valid, message = validate_google_slides_pdf(str(pdf_path))

    assert is_valid is True
    assert message == ""


def test_validate_google_slides_pdf_rejects_skia(tmp_path):
    pdf_path = _create_pdf(tmp_path, {"/Producer": "Skia/PDF m123"})

    is_valid, message = validate_google_slides_pdf(str(pdf_path))

    assert is_valid is False
    assert "Google Slides" in message


def test_validate_google_slides_pdf_handles_unreadable(tmp_path):
    pdf_path = tmp_path / "not_a_pdf.pdf"
    pdf_path.write_text("not a real pdf")

    is_valid, message = validate_google_slides_pdf(str(pdf_path))

    assert is_valid is False
    assert "Unable to read PDF metadata" in message
