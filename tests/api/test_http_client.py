"""Tests for the HTTP client utilities."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api.http_client import (
    INTERNAL_SERVICE_TIMEOUT,
    close_shared_client,
    get_shared_client,
    retry_on_timeout,
)


class TestInternalServiceTimeout:
    """Tests for timeout configuration."""

    def test_timeout_values(self):
        """Test that timeout values are configured correctly."""
        assert INTERNAL_SERVICE_TIMEOUT.connect == 10.0
        assert INTERNAL_SERVICE_TIMEOUT.read == 30.0
        assert INTERNAL_SERVICE_TIMEOUT.write == 30.0
        assert INTERNAL_SERVICE_TIMEOUT.pool == 10.0


class TestSharedClient:
    """Tests for shared HTTP client management."""

    @pytest.mark.asyncio
    async def test_get_shared_client_creates_client(self):
        """Test that get_shared_client creates a new client."""
        # Reset global client state
        import app.api.http_client as http_client_module

        http_client_module._shared_client = None

        client = await get_shared_client()
        assert client is not None
        assert isinstance(client, httpx.AsyncClient)

        # Cleanup
        await close_shared_client()

    @pytest.mark.asyncio
    async def test_get_shared_client_returns_same_instance(self):
        """Test that get_shared_client returns the same instance."""
        import app.api.http_client as http_client_module

        http_client_module._shared_client = None

        client1 = await get_shared_client()
        client2 = await get_shared_client()
        assert client1 is client2

        # Cleanup
        await close_shared_client()

    @pytest.mark.asyncio
    async def test_close_shared_client(self):
        """Test that close_shared_client properly closes the client."""
        import app.api.http_client as http_client_module

        http_client_module._shared_client = None

        client = await get_shared_client()
        assert not client.is_closed

        await close_shared_client()
        assert http_client_module._shared_client is None

    @pytest.mark.asyncio
    async def test_get_shared_client_recreates_after_close(self):
        """Test that a new client is created after closing."""
        import app.api.http_client as http_client_module

        http_client_module._shared_client = None

        client1 = await get_shared_client()
        await close_shared_client()

        client2 = await get_shared_client()
        assert client1 is not client2

        # Cleanup
        await close_shared_client()


class TestRetryOnTimeout:
    """Tests for retry_on_timeout function."""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        """Test that function returns on first successful attempt."""
        mock_func = AsyncMock(return_value="success")

        result = await retry_on_timeout(mock_func)

        assert result == "success"
        assert mock_func.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_connect_timeout(self):
        """Test that ConnectTimeout triggers retry."""
        mock_func = AsyncMock(
            side_effect=[
                httpx.ConnectTimeout("Connection timed out"),
                "success",
            ]
        )

        with patch("app.api.http_client.asyncio.sleep", new_callable=AsyncMock):
            result = await retry_on_timeout(mock_func, base_delay=0.01)

        assert result == "success"
        assert mock_func.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_read_timeout(self):
        """Test that ReadTimeout triggers retry."""
        mock_func = AsyncMock(
            side_effect=[
                httpx.ReadTimeout("Read timed out"),
                httpx.ReadTimeout("Read timed out"),
                "success",
            ]
        )

        with patch("app.api.http_client.asyncio.sleep", new_callable=AsyncMock):
            result = await retry_on_timeout(mock_func, base_delay=0.01)

        assert result == "success"
        assert mock_func.call_count == 3

    @pytest.mark.asyncio
    async def test_retry_on_connect_error(self):
        """Test that ConnectError triggers retry."""
        mock_func = AsyncMock(
            side_effect=[
                httpx.ConnectError("Connection refused"),
                "success",
            ]
        )

        with patch("app.api.http_client.asyncio.sleep", new_callable=AsyncMock):
            result = await retry_on_timeout(mock_func, base_delay=0.01)

        assert result == "success"
        assert mock_func.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        """Test that exception is raised after max retries exhausted."""
        mock_func = AsyncMock(side_effect=httpx.ReadTimeout("Read timed out"))

        mock_notify = MagicMock()
        with patch("app.api.http_client.asyncio.sleep", new_callable=AsyncMock):
            with patch(
                "app.api.http_client._get_notify_exception", return_value=mock_notify
            ):
                with pytest.raises(httpx.ReadTimeout):
                    await retry_on_timeout(mock_func, max_retries=2, base_delay=0.01)

        # 3 attempts total (initial + 2 retries)
        assert mock_func.call_count == 3
        # Notification sent on final failure
        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_notification_when_disabled(self):
        """Test that notification can be disabled."""
        mock_func = AsyncMock(side_effect=httpx.ReadTimeout("Read timed out"))

        mock_notify = MagicMock()
        with patch("app.api.http_client.asyncio.sleep", new_callable=AsyncMock):
            with patch(
                "app.api.http_client._get_notify_exception", return_value=mock_notify
            ):
                with pytest.raises(httpx.ReadTimeout):
                    await retry_on_timeout(
                        mock_func,
                        max_retries=1,
                        base_delay=0.01,
                        notify_on_final_failure=False,
                    )

        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_retry_on_other_exceptions(self):
        """Test that non-timeout exceptions are raised immediately."""
        mock_func = AsyncMock(side_effect=ValueError("Some other error"))

        with pytest.raises(ValueError):
            await retry_on_timeout(mock_func)

        # Only one attempt, no retry
        assert mock_func.call_count == 1

    @pytest.mark.asyncio
    async def test_exponential_backoff(self):
        """Test that delays increase exponentially."""
        mock_func = AsyncMock(
            side_effect=[
                httpx.ReadTimeout("timeout"),
                httpx.ReadTimeout("timeout"),
                httpx.ReadTimeout("timeout"),
                "success",
            ]
        )
        sleep_calls = []

        async def track_sleep(delay):
            sleep_calls.append(delay)

        with patch("app.api.http_client.asyncio.sleep", side_effect=track_sleep):
            result = await retry_on_timeout(mock_func, base_delay=1.0, max_delay=10.0)

        assert result == "success"
        # Delays should be: 1.0 * 2^0 = 1.0, 1.0 * 2^1 = 2.0, 1.0 * 2^2 = 4.0
        assert sleep_calls == [1.0, 2.0, 4.0]

    @pytest.mark.asyncio
    async def test_max_delay_caps_backoff(self):
        """Test that max_delay caps the exponential backoff."""
        mock_func = AsyncMock(
            side_effect=[
                httpx.ReadTimeout("timeout"),
                httpx.ReadTimeout("timeout"),
                httpx.ReadTimeout("timeout"),
                "success",
            ]
        )
        sleep_calls = []

        async def track_sleep(delay):
            sleep_calls.append(delay)

        with patch("app.api.http_client.asyncio.sleep", side_effect=track_sleep):
            result = await retry_on_timeout(mock_func, base_delay=2.0, max_delay=3.0)

        assert result == "success"
        # Delays should be capped: 2.0, 3.0 (capped from 4.0), 3.0 (capped from 8.0)
        assert sleep_calls == [2.0, 3.0, 3.0]

    @pytest.mark.asyncio
    async def test_passes_args_and_kwargs(self):
        """Test that arguments are passed correctly to the function."""
        mock_func = AsyncMock(return_value="result")

        await retry_on_timeout(mock_func, "arg1", "arg2", kwarg1="value1")

        mock_func.assert_called_once_with("arg1", "arg2", kwarg1="value1")
