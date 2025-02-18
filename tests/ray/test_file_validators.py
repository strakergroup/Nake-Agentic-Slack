from app.ray.file_validators import validate_json


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
