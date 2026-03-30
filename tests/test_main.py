"""Tests for app/main.py - FastAPI application setup."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Import the app after patching buglog
with patch("app.main.buglog.init"):
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
    async def test_buglog_middleware_calls_notify_exception_when_call_next_raises(self):
        """``buglog_middleware`` forwards failures to ``notify_exception`` then re-raises."""
        from app.main import buglog_middleware

        request = MagicMock()

        async def call_next(_request):
            raise RuntimeError("unhandled handler error for buglog middleware test")

        with patch("app.main.notify_exception") as mock_notify:
            with pytest.raises(RuntimeError, match="unhandled handler error"):
                await buglog_middleware(request, call_next)

        mock_notify.assert_called_once()
        err = mock_notify.call_args[0][0]
        assert isinstance(err, RuntimeError)
        assert str(err) == "unhandled handler error for buglog middleware test"

    @pytest.mark.skipif(
        sys.version_info < (3, 11),
        reason="ExceptionGroup requires Python 3.11+",
    )
    @pytest.mark.asyncio
    async def test_buglog_middleware_exception_group_notifies_leaf_exceptions(self):
        from app.main import buglog_middleware

        request = MagicMock()

        async def call_next(_request):
            raise ExceptionGroup(
                "eg",
                [ValueError("a"), ValueError("b")],
            )

        with patch("app.main.notify_exception") as mock_notify:
            with pytest.raises(ExceptionGroup):
                await buglog_middleware(request, call_next)

        assert mock_notify.call_count == 2
        assert {mock_notify.call_args_list[i][0][0].args[0] for i in range(2)} == {
            "a",
            "b",
        }

    @pytest.mark.skipif(
        sys.version_info < (3, 11),
        reason="ExceptionGroup requires Python 3.11+",
    )
    @pytest.mark.asyncio
    async def test_buglog_middleware_nested_exception_group(self):
        from app.main import buglog_middleware

        request = MagicMock()

        async def call_next(_request):
            raise ExceptionGroup(
                "outer",
                [ExceptionGroup("inner", [ValueError("nested_leaf")])],
            )

        with patch("app.main.notify_exception") as mock_notify:
            with pytest.raises(ExceptionGroup):
                await buglog_middleware(request, call_next)

        mock_notify.assert_called_once()
        assert mock_notify.call_args[0][0].args[0] == "nested_leaf"

    @pytest.mark.skipif(
        sys.version_info < (3, 11),
        reason="ExceptionGroup requires Python 3.11+",
    )
    @pytest.mark.asyncio
    async def test_buglog_middleware_base_exception_group_skips_non_exception_leaves(
        self,
    ):
        """``ExceptionGroup`` cannot mix in ``SystemExit``; ``BaseExceptionGroup`` can."""
        from app.main import buglog_middleware

        request = MagicMock()

        async def call_next(_request):
            raise BaseExceptionGroup(
                "mixed",
                [ValueError("notify_this"), SystemExit(42)],
            )

        with patch("app.main.notify_exception") as mock_notify:
            with pytest.raises(BaseExceptionGroup):
                await buglog_middleware(request, call_next)

        mock_notify.assert_called_once()
        assert mock_notify.call_args[0][0].args[0] == "notify_this"

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
