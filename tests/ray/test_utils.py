import datetime
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse
from uuid import uuid4

import pytest

import app  # Bug - circular import
import app.ray.utils
from app.auth.connector import get_group_mt_engine, get_job_group_quote_settings
from app.config import domains
from app.ray.utils import (
    GRIDFS_UPLOAD_EXPIRY_DAYS,
    upload_to_file_server,
    validate_file,
)
from app.translate import Translator, _, translator_var


def test_get_job_url():
    job_id = str(uuid4())
    client_id = str(uuid4())
    url = app.ray.utils.get_job_url(job_id, client_id)
    parsed_url = urlparse(url)
    assert url.startswith(domains.languagecloud)
    assert f"j={job_id}" in parsed_url.query
    assert f"member_id={client_id}" in parsed_url.query


def test_get_job_url_no_client_id():
    job_id = str(uuid4())
    url = app.ray.utils.get_job_url(job_id)
    url2 = app.ray.utils.get_job_url(job_id, "")
    parsed_url = urlparse(url)
    assert url.startswith(domains.languagecloud)
    assert f"j={job_id}" in parsed_url.query
    assert url == url2


def test_format_currency_symbol():
    assert app.ray.utils.format_currency_symbol("USD") == "USD"
    assert app.ray.utils.format_currency_symbol("USD_Other") == "USD"
    assert app.ray.utils.format_currency_symbol("EUR") == "EUR"
    assert app.ray.utils.format_currency_symbol("EUR_DE") == "EUR"
    assert app.ray.utils.format_currency_symbol("NZD") == "NZD"
    assert app.ray.utils.format_currency_symbol("AUD") == "AUD"


def test_format_currency():
    assert app.ray.utils.format_currency(3.5, "USD") == "US$3.50"
    assert app.ray.utils.format_currency(3.5, "USD_Other") == "US$3.50"
    assert app.ray.utils.format_currency(3.5, "NZD") == "NZ$3.50"
    assert app.ray.utils.format_currency(2.5, "EUR") == "€2.50"
    assert app.ray.utils.format_currency(2.501, "GBP") == "£2.50"
    assert app.ray.utils.format_currency(10, "AUD") == "A$10.00"
    assert app.ray.utils.format_currency(-420, "JPY") == "-JP¥420"


def test_format_job_status():
    assert app.ray.utils.format_job_status("LEAD") == "Quote Requested"
    assert app.ray.utils.format_job_status("IN_PROGRESS") == "In Progress"
    assert app.ray.utils.format_job_status("COMPLETED") == "Completed"
    assert app.ray.utils.format_job_status("OTHER_STATUS") == "OTHER_STATUS"


def test_format_datetime_slack():
    naive_date = datetime.datetime.now()
    aware_utc_date = naive_date.replace(tzinfo=datetime.timezone.utc)
    timestamp = int(aware_utc_date.timestamp())
    fallback = naive_date.strftime("%Y-%m-%d %H:%M UTC")
    assert (
        app.ray.utils.format_datetime_slack(naive_date)
        == f"<!date^{timestamp}^{{date}} {{time}}|{fallback}>"
    )
    assert (
        app.ray.utils.format_datetime_slack(aware_utc_date)
        == f"<!date^{timestamp}^{{date}} {{time}}|{fallback}>"
    )


def test_format_job_due_date_slack():
    current_date = datetime.datetime.now(datetime.timezone.utc)
    tomorrow = current_date + datetime.timedelta(days=1)
    yesterday = current_date - datetime.timedelta(days=1)
    next_week = current_date + datetime.timedelta(days=7)

    # Show "in X hour(s)" if date within 48 hours.
    assert app.ray.utils.format_job_due_date_slack(tomorrow).endswith("hour(s)")
    assert app.ray.utils.format_job_due_date_slack(current_date).endswith(">")
    assert app.ray.utils.format_job_due_date_slack(yesterday).endswith(">")
    assert app.ray.utils.format_job_due_date_slack(next_week).endswith(">")

    # Show traffic lights if status is "IN_PROGRESS".
    assert app.ray.utils.format_job_due_date_slack(
        target_date=yesterday, job_status="IN_PROGRESS", traffic_light=True
    ).startswith("<!date")
    assert app.ray.utils.format_job_due_date_slack(
        target_date=tomorrow, job_status="IN_PROGRESS", traffic_light=True
    ).startswith("in")
    assert not app.ray.utils.format_job_due_date_slack(
        target_date=yesterday, traffic_light=True
    ).startswith(":")
    # Don't show lights if the traffic_light argument is False.
    assert not app.ray.utils.format_job_due_date_slack(
        target_date=yesterday, job_status="IN_PROGRESS", traffic_light=False
    ).startswith(":")


def test_translations():
    # Save the original translator token to restore it later
    original_token = translator_var.set(Translator("jp"))
    try:
        user_details = "test"
        user_link = "test"
        input = "Your account {user_details} is now disconnected from {user_link}."
        assert _(input) != input
    finally:
        # Restore the original translator to prevent affecting other tests
        translator_var.reset(original_token)


def test_get_job_group_quote_settings():
    # Assuming 'test_job_uuid' exists in your test database and is associated with a job group
    test_job_uuid = "989A1445-B699-41F4-8E1C-105FD530E450"
    settings = get_job_group_quote_settings(test_job_uuid)
    assert settings is not None  # Adjust this assertion based on expected results
    print(settings)


def test_get_group_mt():
    # Assuming 'test_group' exists in your test database and is associated with a job group
    test_group = "571E9A50-85BA-4356-887411C111D12FDC"
    is_group = False
    settings = get_group_mt_engine(test_group, is_group)
    assert settings is not None  # Adjust this assertion based on expected results
    print(settings)


def test_validate_file():
    # Test supported file types with no validator
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("content")
        txt_file = f.name
    try:
        assert validate_file(txt_file) == (True, True, "")
    finally:
        os.unlink(txt_file)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".docx", delete=False) as f:
        docx_file = f.name
    try:
        assert validate_file(docx_file) == (True, True, "")
    finally:
        os.unlink(docx_file)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
        f.write("")
        html_file = f.name
    try:
        assert validate_file(html_file) == (True, True, "")
    finally:
        os.unlink(html_file)

    with tempfile.NamedTemporaryFile(mode="wb", suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4\n" + b"0" * (512 * 1024))
        small_pdf_file = f.name
    try:
        assert validate_file(small_pdf_file, max_pdf_size_bytes=1024 * 1024) == (
            True,
            True,
            "",
        )
    finally:
        os.unlink(small_pdf_file)

    with tempfile.NamedTemporaryFile(mode="wb", suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024))
        large_pdf_file = f.name
    try:
        result = validate_file(large_pdf_file, max_pdf_size_bytes=1024 * 1024)
        assert result[0] is True
        assert result[1] is False
        assert "exceeds the current PDF limit of 1MB" in result[2]
    finally:
        os.unlink(large_pdf_file)

    # Test unsupported file types
    with tempfile.NamedTemporaryFile(mode="w", suffix=".invalid", delete=False) as f:
        invalid_file = f.name
    try:
        assert validate_file(invalid_file) == (
            False,
            False,
            "Unsupported file type: invalid",
        )
    finally:
        os.unlink(invalid_file)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".exe", delete=False) as f:
        exe_file = f.name
    try:
        assert validate_file(exe_file) == (
            False,
            False,
            "Unsupported file type: exe",
        )
    finally:
        os.unlink(exe_file)

    # Test JSON validation
    valid_json = '{"key": "value"}'
    invalid_json = "{key: value}"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(valid_json)
        valid_json_file = f.name
    try:
        assert validate_file(valid_json_file) == (True, True, "")
    finally:
        os.unlink(valid_json_file)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(invalid_json)
        invalid_json_file = f.name
    try:
        result = validate_file(invalid_json_file)
        assert result[0] is True  # Extension is valid
        assert result[1] is False  # Content is invalid
        assert "Invalid JSON" in result[2]
    finally:
        os.unlink(invalid_json_file)

    # Test case insensitivity
    with tempfile.NamedTemporaryFile(mode="w", suffix=".TXT", delete=False) as f:
        f.write("content")
        txt_upper_file = f.name
    try:
        assert validate_file(txt_upper_file) == (True, True, "")
    finally:
        os.unlink(txt_upper_file)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".JSON", delete=False) as f:
        f.write(valid_json)
        json_upper_file = f.name
    try:
        assert validate_file(json_upper_file) == (True, True, "")
    finally:
        os.unlink(json_upper_file)


@pytest.mark.asyncio
async def test_upload_to_file_server_sets_expiry():
    """upload_to_file_server must send expires_at (~7 days) to the /gridfs endpoint."""
    fake_file_id = "abc123"
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": fake_file_id}

    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(b"test content")
        tmp_path = f.name

    try:
        with patch("app.ray.utils.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = False
            mock_client.put.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = await upload_to_file_server(tmp_path)

            assert result == fake_file_id

            call_kwargs = mock_client.put.call_args
            data = call_kwargs.kwargs.get("data", {})

            assert "expires_at" in data, "expires_at must be sent to /gridfs"

            expires_at = datetime.datetime.fromisoformat(data["expires_at"])
            print(f"expires_at sent to /gridfs: {expires_at.isoformat()}")
            now = datetime.datetime.now(datetime.timezone.utc)
            delta_seconds = (expires_at - now).total_seconds()
            expected_seconds = GRIDFS_UPLOAD_EXPIRY_DAYS * 86400
            assert expected_seconds - 60 <= delta_seconds <= expected_seconds + 60, (
                f"Expected ~{GRIDFS_UPLOAD_EXPIRY_DAYS} day expiry, got {delta_seconds:.0f}s"
            )
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_upload_to_file_server_raises_on_missing_id():
    """upload_to_file_server must raise ValueError when the response has no 'id'."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}

    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(b"test content")
        tmp_path = f.name

    try:
        with patch("app.ray.utils.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = False
            mock_client.put.return_value = mock_response
            mock_client_cls.return_value = mock_client

            with pytest.raises(ValueError, match="'id'"):
                await upload_to_file_server(tmp_path)
    finally:
        os.unlink(tmp_path)
