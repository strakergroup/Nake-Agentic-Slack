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


@pytest.fixture(autouse=True)
def mock_saq_worker_status():
    with patch("app.routers.health.worker_status") as mock_status:
        mock_status.return_value = {
            "enabled": True,
            "expected_count": 5,
            "running_count": 5,
            "all_running": True,
            "workers": [],
        }
        yield mock_status


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
            assert data["x"] == 1
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
                assert data["x"] == 1
                assert "environment" in data
                assert "errors" in data
                assert "info" in data
                assert data["info"]["saq_workers"]["all_running"] is True

    @pytest.mark.asyncio
    async def test_health_check_fails_when_saq_workers_are_not_running(
        self, client, mock_saq_worker_status
    ):
        mock_saq_worker_status.return_value = {
            "enabled": True,
            "expected_count": 5,
            "running_count": 3,
            "all_running": False,
            "workers": [
                {
                    "label": "file-delivery",
                    "queue_name": "slack-ray-translator-file-delivery",
                    "running": False,
                    "done": True,
                    "cancelled": True,
                }
            ],
        }

        response = await client.get("/health")

        assert response.status_code == 500
        data = response.json()
        assert data["message"] == "There are some issues"
        assert "errors" not in data

    @pytest.mark.asyncio
    async def test_health_check_slack_api_error(self, client):
        """Slack is not checked in health_check yet; a broken api.test mock must not run."""
        with patch(
            "app.routers.health.slack_app.client.api_test", new_callable=AsyncMock
        ) as mock_api_test:
            mock_api_test.side_effect = Exception("Slack API error")

            response = await client.get("/health")

            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "OK"
            assert data["x"] == 1
            mock_api_test.assert_not_called()

    @pytest.mark.asyncio
    async def test_health_check_slack_api_error_with_password(self, client):
        """Same with password: errors dict stays empty; Slack mock unused."""
        with patch(
            "app.routers.health.config.health_check_password.get_secret_value"
        ) as mock_get_secret:
            mock_get_secret.return_value = "test-password"
            with patch(
                "app.routers.health.slack_app.client.api_test", new_callable=AsyncMock
            ) as mock_api_test:
                mock_api_test.side_effect = Exception("Slack API error")

                response = await client.get("/health?password=test-password")

                assert response.status_code == 200
                data = response.json()
                assert data["message"] == "OK"
                assert data["x"] == 1
                assert data.get("errors", {}) == {}
                mock_api_test.assert_not_called()

    @pytest.mark.asyncio
    async def test_health_check_slack_api_bad_status(self, client):
        """Non-200 api.test mock is irrelevant until _check_slack_api is awaited."""
        with patch(
            "app.routers.health.slack_app.client.api_test", new_callable=AsyncMock
        ) as mock_api_test:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_api_test.return_value = mock_response

            response = await client.get("/health")

            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "OK"
            assert data["x"] == 1
            mock_api_test.assert_not_called()

    @pytest.mark.asyncio
    async def test_health_check_slack_api_bad_status_with_password(self, client):
        """With password: still no slack_api error until router calls _check_slack_api."""
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

                assert response.status_code == 200
                data = response.json()
                assert data["message"] == "OK"
                assert data["x"] == 1
                assert "slack_api" not in data.get("errors", {})
                mock_api_test.assert_not_called()

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
                assert data["x"] == 1
                assert "environment" not in data  # Hidden with wrong password
                assert "errors" not in data
                assert "info" not in data
