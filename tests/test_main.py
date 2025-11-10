"""Tests for app/main.py - FastAPI application setup."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Import the app after patching buglog
with patch("app.main.buglog.init"):
    with patch("app.main._wrap_notify_exception"):
        from app.main import app


@pytest_asyncio.fixture
async def client():
    """Create an async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestMainApp:
    """Tests for main FastAPI application."""

    @pytest.mark.asyncio
    async def test_root_endpoint(self, client):
        """Test root endpoint returns correct message."""
        response = await client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Straker Translate API"

    @pytest.mark.asyncio
    async def test_app_includes_routers(self, client):
        """Test that app includes all routers."""
        # Test health router
        with patch(
            "app.routers.health.slack_app.client.api_test", new_callable=AsyncMock
        ) as mock_api_test:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_api_test.return_value = mock_response

            response = await client.get("/health")
            assert response.status_code == 200

        # Test slack router redirect
        with patch(
            "app.routers.slack.domains.slack_ray_translator", "https://test.example.com"
        ):
            response = await client.get("/slack/openid/connect", follow_redirects=False)
            assert response.status_code == 307

        # Test ray router (should exist but may require auth)
        # Just verify it's registered by checking 404 vs 405
        response = await client.get("/ray/events")
        # Should get 422 (validation error) or 401 (auth error), not 404
        assert response.status_code != 404
