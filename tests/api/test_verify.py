"""Tests for verify API functions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.verify import VerifyAPIError, create_human_job


@pytest.mark.asyncio
async def test_create_human_job_sends_multiple_file_and_languages_fields():
    """Test that create_human_job sends multiple file_and_languages as separate form fields."""
    # Setup - import RayClient here to avoid circular dependency
    from app.auth.connector import RayClient

    mock_ray_client = MagicMock(spec=RayClient)
    mock_ray_client.id_token = "test-token"
    job_uuid = "test-job-uuid"
    file_and_languages = [
        "file-uuid-1:lang-uuid-1",
        "file-uuid-2:lang-uuid-2",
        "file-uuid-3:lang-uuid-3",
    ]

    # Mock httpx response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(
        return_value={"tp_job_uuid": "tp-job-123", "status": "Success"}
    )
    mock_response.raise_for_status = MagicMock()

    # Mock httpx client
    with patch("app.api.verify.domains") as mock_domains:
        mock_domains.verify_api = "https://verify-api.test.com"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            # Execute
            result = await create_human_job(
                mock_ray_client, job_uuid, file_and_languages
            )

            # Verify
            assert result == {"tp_job_uuid": "tp-job-123", "status": "Success"}

            # Verify the POST call was made with correct parameters
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args

            # Check URL
            assert (
                call_args[0][0]
                == "https://verify-api.test.com/automation/service/create-human-job"
            )

            # Check headers
            assert call_args[1]["headers"] == {"Authorization": "Bearer test-token"}

            # Check files parameter - should be a list of tuples
            files_data = call_args[1]["files"]
            assert isinstance(files_data, list)
            assert len(files_data) == 6  # 3 regular fields + 3 file_and_languages

            # Verify all fields are present
            field_names = [field[0] for field in files_data]
            assert "job_uuid" in field_names
            assert "service_uuid" in field_names
            assert "purchase_order_number" in field_names
            assert field_names.count("file_and_languages") == 3

            # Verify file_and_languages values
            file_and_lang_values = [
                field[1] for field in files_data if field[0] == "file_and_languages"
            ]
            assert set(file_and_lang_values) == set(file_and_languages)


@pytest.mark.asyncio
async def test_create_human_job_handles_401_error():
    """Test that create_human_job raises VerifyAPIError on 401."""
    from app.auth.connector import RayClient

    mock_ray_client = MagicMock(spec=RayClient)
    mock_ray_client.id_token = "test-token"

    mock_response = MagicMock()
    mock_response.status_code = 401

    with patch("app.api.verify.domains") as mock_domains:
        mock_domains.verify_api = "https://verify-api.test.com"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with pytest.raises(VerifyAPIError) as exc_info:
                await create_human_job(mock_ray_client, "job-uuid", ["file:lang"])

            assert exc_info.value.status_code == 401
            assert "Unauthorized" in exc_info.value.message


@pytest.mark.asyncio
async def test_create_human_job_handles_403_error():
    """Test that create_human_job raises VerifyAPIError on 403."""
    from app.auth.connector import RayClient

    mock_ray_client = MagicMock(spec=RayClient)
    mock_ray_client.id_token = "test-token"

    mock_response = MagicMock()
    mock_response.status_code = 403

    with patch("app.api.verify.domains") as mock_domains:
        mock_domains.verify_api = "https://verify-api.test.com"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with pytest.raises(VerifyAPIError) as exc_info:
                await create_human_job(mock_ray_client, "job-uuid", ["file:lang"])

            assert exc_info.value.status_code == 403
            assert "Forbidden" in exc_info.value.message
