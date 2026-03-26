from PyPDF2 import PdfWriter

from app.ray.file_validators import validate_json, validate_pdf


def _write_json_file(tmp_path, content: str, filename: str = "sample.json") -> str:
    json_path = tmp_path / filename
    json_path.write_text(content, encoding="utf-8")
    return str(json_path)


def test_validate_json_valid_string(tmp_path):
    """Test validation of valid JSON content."""
    json_path = _write_json_file(tmp_path, '{"key": "value"}')

    is_valid, message = validate_json(json_path)
    assert is_valid is True
    assert message == ""


def test_validate_json_valid_path(tmp_path):
    """Test validation of valid JSON file path."""
    json_path = _write_json_file(tmp_path, '{"key": "value"}', filename="valid.json")

    is_valid, message = validate_json(json_path)
    assert is_valid is True
    assert message == ""


def test_validate_json_invalid_structure(tmp_path):
    """Test validation of invalid JSON structure."""
    json_path = _write_json_file(tmp_path, "invalid json", filename="invalid.json")

    is_valid, message = validate_json(json_path)
    assert is_valid is False
    assert "Invalid JSON. Please fix the issue and resubmit the file." in message


def test_validate_json_invalid_bytes(tmp_path):
    """Test validation with non-UTF8 bytes."""
    json_path = tmp_path / "invalid_bytes.json"
    json_path.write_bytes(b"\x80\x81")

    is_valid, message = validate_json(str(json_path))
    assert is_valid is False
    # ijson reports invalid bytes as a parse error, not UnicodeDecodeError
    assert "Error: Invalid JSON. Please fix the issue and resubmit the file." in message


def test_validate_json_complex(tmp_path):
    """Test validation of complex JSON."""
    complex_json = """
    {
        "name": "test",
        "numbers": [1, 2, 3],
        "nested": {"a": 1}
    }
    """
    json_path = _write_json_file(tmp_path, complex_json, filename="complex.json")

    is_valid, message = validate_json(json_path)
    assert is_valid is True
    assert message == ""


def _create_pdf(tmp_path, metadata=None, mediabox=None):
    pdf_path = tmp_path / "sample.pdf"
    writer = PdfWriter()
    width, height = mediabox if mediabox else (72, 72)
    writer.add_blank_page(width=width, height=height)
    if metadata:
        writer.add_metadata(metadata)
    with pdf_path.open("wb") as f:
        writer.write(f)
    return pdf_path


def test_validate_pdf_valid(tmp_path):
    pdf_path = _create_pdf(tmp_path, {"/Producer": "Acme PDF Engine"})

    is_valid, message = validate_pdf(str(pdf_path))

    assert is_valid is True
    assert message == ""


def test_validate_pdf_rejects_google_slides(tmp_path):
    pdf_path = _create_pdf(
        tmp_path,
        {"/Creator": "Google Slides"},
        mediabox=(1600, 900),
    )

    is_valid, message = validate_pdf(str(pdf_path))

    assert is_valid is False
    assert "Google" in message
    assert "16:9" in message


def test_validate_pdf_allows_non_slide_google_pdf(tmp_path):
    pdf_path = _create_pdf(
        tmp_path,
        {"/Creator": "Google Docs"},
        mediabox=(850, 1100),
    )

    is_valid, message = validate_pdf(str(pdf_path))

    assert is_valid is True
    assert message == ""


def test_validate_pdf_handles_unreadable(tmp_path):
    pdf_path = tmp_path / "not_a_pdf.pdf"
    pdf_path.write_text("not a real pdf")

    is_valid, message = validate_pdf(str(pdf_path))

    assert is_valid is False
    assert "Unable to read PDF metadata" in message
