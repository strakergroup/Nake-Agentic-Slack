"""Tests for app/routers/health.py - Health check endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.routers.health import router


@pytest.fixture
def app():
    """Create a FastAPI app with the health router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest_asyncio.fixture
async def client(app):
    """Create an async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestHealthCheck:
    """Tests for /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_check_success(self, client):
        """Test health check with all services OK."""
        with patch(
            "app.routers.health.slack_app.client.api_test", new_callable=AsyncMock
        ) as mock_api_test:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_api_test.return_value = mock_response

            response = await client.get("/health")

            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "OK"
            assert "environment" not in data  # Hidden without password
            assert "errors" not in data
            assert "info" not in data

    @pytest.mark.asyncio
    async def test_health_check_with_password(self, client):
        """Test health check with password shows details."""
        with patch(
            "app.routers.health.config.health_check_password.get_secret_value"
        ) as mock_get_secret:
            mock_get_secret.return_value = "test-password"
            with patch(
                "app.routers.health.slack_app.client.api_test", new_callable=AsyncMock
            ) as mock_api_test:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_api_test.return_value = mock_response

                response = await client.get("/health?password=test-password")

                assert response.status_code == 200
                data = response.json()
                assert data["message"] == "OK"
                assert "environment" in data
                assert "errors" in data
                assert "info" in data

    @pytest.mark.asyncio
    async def test_health_check_slack_api_error(self, client):
        """Test health check when Slack API fails."""
        with patch(
            "app.routers.health.slack_app.client.api_test", new_callable=AsyncMock
        ) as mock_api_test:
            mock_api_test.side_effect = Exception("Slack API error")

            response = await client.get("/health")

            assert response.status_code == 500
            data = response.json()
            assert data["message"] == "There are some issues"
            # Errors are hidden without password, but message indicates issues

    @pytest.mark.asyncio
    async def test_health_check_slack_api_error_with_password(self, client):
        """Test health check when Slack API fails, with password to see details."""
        with patch(
            "app.routers.health.config.health_check_password.get_secret_value"
        ) as mock_get_secret:
            mock_get_secret.return_value = "test-password"
            with patch(
                "app.routers.health.slack_app.client.api_test", new_callable=AsyncMock
            ) as mock_api_test:
                mock_api_test.side_effect = Exception("Slack API error")

                response = await client.get("/health?password=test-password")

                assert response.status_code == 500
                data = response.json()
                assert data["message"] == "There are some issues"
                assert "slack_api" in data["errors"]
                assert data["errors"]["slack_api"] == "Slack API error"

    @pytest.mark.asyncio
    async def test_health_check_slack_api_bad_status(self, client):
        """Test health check when Slack API returns bad status code."""
        with patch(
            "app.routers.health.slack_app.client.api_test", new_callable=AsyncMock
        ) as mock_api_test:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_api_test.return_value = mock_response

            response = await client.get("/health")

            assert response.status_code == 500
            data = response.json()
            assert data["message"] == "There are some issues"
            # Errors are hidden without password, but message indicates issues

    @pytest.mark.asyncio
    async def test_health_check_slack_api_bad_status_with_password(self, client):
        """Test health check when Slack API returns bad status code, with password."""
        with patch(
            "app.routers.health.config.health_check_password.get_secret_value"
        ) as mock_get_secret:
            mock_get_secret.return_value = "test-password"
            with patch(
                "app.routers.health.slack_app.client.api_test", new_callable=AsyncMock
            ) as mock_api_test:
                mock_response = MagicMock()
                mock_response.status_code = 500
                mock_api_test.return_value = mock_response

                response = await client.get("/health?password=test-password")

                assert response.status_code == 500
                data = response.json()
                assert data["message"] == "There are some issues"
                assert "slack_api" in data["errors"]
                assert (
                    "api.test returned the status code: 500"
                    in data["errors"]["slack_api"]
                )

    @pytest.mark.asyncio
    async def test_health_check_wrong_password(self, client):
        """Test health check with wrong password hides details."""
        with patch(
            "app.routers.health.config.health_check_password.get_secret_value"
        ) as mock_get_secret:
            mock_get_secret.return_value = "correct-password"
            with patch(
                "app.routers.health.slack_app.client.api_test", new_callable=AsyncMock
            ) as mock_api_test:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_api_test.return_value = mock_response

                response = await client.get("/health?password=wrong-password")

                assert response.status_code == 200
                data = response.json()
                assert data["message"] == "OK"
                assert "environment" not in data  # Hidden with wrong password
                assert "errors" not in data
                assert "info" not in data
