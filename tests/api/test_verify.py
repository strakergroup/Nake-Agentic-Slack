"""Tests for verify API functions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.verify import VerifyAPIError, create_human_job


@pytest.mark.asyncio
async def test_create_human_job_sends_multiple_file_and_languages_fields():
    """Test that create_human_job POSTs the expected URL, headers, and form ``data``."""
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
                mock_ray_client,
                job_uuid,
                file_and_languages,
                purchase_order_number="alpha.xlf, beta.xlf",
            )

            # Verify
            assert result == {"tp_job_uuid": "tp-job-123", "status": "Success"}

            # Verify the POST call was made with correct parameters
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args

            # Check URL (first positional argument)
            assert (
                call_args[0][0]
                == "https://verify-api.test.com/automation/service/create-human-job"
            )

            # Check headers
            kwargs = call_args[1] if len(call_args) > 1 else call_args.kwargs
            assert kwargs["headers"] == {"Authorization": "Bearer test-token"}

            # Implementation sends a form body via data= (not multipart files=)
            assert "data" in kwargs, f"Expected data= on httpx.post. Got: {list(kwargs.keys())}"
            posted = kwargs["data"]
            assert posted["job_uuid"] == job_uuid
            assert posted["file_and_languages"] == file_and_languages
            assert posted["purchase_order_number"] == "alpha.xlf, beta.xlf"
            assert posted["service_uuid"] == "37f2e44b-ba3c-42b1-83c7-d3023298292f"


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
