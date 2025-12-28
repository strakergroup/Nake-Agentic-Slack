"""Tests for app/routers/ray.py - RAY platform event and callback endpoints."""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.auth.connector import RayConnection, RaySuperGroup, SlackUser
from app.dependencies import RayEvent, RayEventAuth
from app.models import TranscriptionTaskInfo
from app.ray.events.models import (
    ClientGroup,
)
from app.routers.ray import RayCallback, api_job_callback, ray_events, router


@pytest.fixture
def app():
    """Create a FastAPI app with the router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_slack_user(user_id, team_id):
    """Create a mock SlackUser."""
    return SlackUser(
        user_id=user_id,
        team_id=team_id,
        enterprise_id=None,
        channel_id=user_id,
        bot_token="xoxb-test-token",
        ray_client_id=str(uuid4()),
        ray_username="test.user",
        ray_user_group_id=str(uuid4()),
        is_subscribed=True,
    )


@pytest.fixture
def mock_ray_connection(team_id, ray_client):
    """Create a mock RayConnection."""
    super_group = RaySuperGroup(
        id=str(uuid4()),
        name="Test Group",
        verify_organization_uuid=str(uuid4()),
        enable_verify_in_slack=False,
        slack_team_id=team_id,
        slack_enterprise_id=None,
    )
    return RayConnection(super_group=[super_group], client=ray_client)


@pytest.fixture
def valid_token():
    """Return a valid auth token."""
    return "valid-token"


class TestRayEventsEndpoint:
    """Tests for /ray/events endpoint."""

    @pytest.mark.asyncio
    async def test_ray_events_invalid_token(self, mock_slack_user):
        """Test that invalid token raises 401."""
        event = RayEvent(event="ray:slack:account_connected", data={})

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                auth = RayEventAuth()
                await auth.initialize(event, "invalid-token")
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_ray_events_account_connected_success(
        self, mock_slack_user, mock_ray_connection, user_id, team_id
    ):
        """Test successful account connected event."""
        event_data = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "username": "test.user",
        }
        event = RayEvent(
            event="ray:slack:account_connected",
            data={"client_id": mock_slack_user.ray_client_id, **event_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.get_ray_connection",
                        return_value=mock_ray_connection,
                    ):
                        with patch(
                            "app.routers.ray.AsyncWebClient", return_value=mock_client
                        ):
                            with patch(
                                "app.routers.ray.post_notification_ephemeral",
                                new_callable=AsyncMock,
                            ):
                                auth = RayEventAuth()
                                await auth.initialize(event, "valid-token")

                                # Call the endpoint handler
                                await ray_events(event, auth)

                                # Verify client was created
                                assert auth.slack_user == mock_slack_user

    @pytest.mark.asyncio
    async def test_ray_events_account_connected_no_ray_connection(
        self, mock_slack_user, user_id, team_id
    ):
        """Test account connected event when ray connection is None."""
        event_data = {
            "user_id": user_id,
            "team_id": team_id,
            "enterprise_id": None,
            "username": "test.user",
        }
        event = RayEvent(
            event="ray:slack:account_connected",
            data={"client_id": mock_slack_user.ray_client_id, **event_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch("app.routers.ray.get_ray_connection", return_value=None):
                        with patch(
                            "app.routers.ray.AsyncWebClient", return_value=mock_client
                        ):
                            auth = RayEventAuth()
                            await auth.initialize(event, "valid-token")

                            # Should raise ValueError when ray_connection is None
                            with pytest.raises(
                                ValueError, match="Could not get ray connection"
                            ):
                                await ray_events(event, auth)

    @pytest.mark.asyncio
    async def test_ray_events_account_connected_validation_error(self, mock_slack_user):
        """Test account connected event with invalid data."""
        event = RayEvent(
            event="ray:slack:account_connected",
            data={"client_id": mock_slack_user.ray_client_id, "invalid": "data"},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {"user": {"id": "U123"}}

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        auth = RayEventAuth()
                        await auth.initialize(event, "valid-token")

                        # Should raise HTTPException with 422 for validation error
                        with pytest.raises(HTTPException) as exc_info:
                            await ray_events(event, auth)
                        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_ray_events_client_signup(self, mock_slack_user, user_id, team_id):
        """Test client signup event."""
        signup_data = {
            "client_id": str(uuid4()),
            "username": "new.user",
            "email": "new@example.com",
            "first_name": "New",
            "last_name": "User",
            "groups": [{"uuid": str(uuid4()), "label": "Group 1"}],
        }
        event = RayEvent(
            event="ray:client:signup",
            data={"client_id": mock_slack_user.ray_client_id, **signup_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.post_notification", new_callable=AsyncMock
                        ) as mock_post:
                            auth = RayEventAuth()
                            await auth.initialize(event, "valid-token")

                            await ray_events(event, auth)

                            # Verify notification was sent
                            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_client_approved(self, mock_slack_user, user_id, team_id):
        """Test client approved event."""
        groups = [ClientGroup(uuid=str(uuid4()), label="Group 1")]
        approved_data = {
            "client_id": str(uuid4()),
            "username": "approved.user",
            "groups": [group.model_dump() for group in groups],
        }
        event = RayEvent(
            event="ray:client:approved",
            data={"client_id": mock_slack_user.ray_client_id, **approved_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.post_notification", new_callable=AsyncMock
                        ) as mock_post:
                            auth = RayEventAuth()
                            await auth.initialize(event, "valid-token")

                            await ray_events(event, auth)

                            # Verify notification was sent
                            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_job_status_changed(
        self, mock_slack_user, user_id, team_id
    ):
        """Test job status changed event."""
        status_data = {
            "client_id": str(uuid4()),
            "uuid": str(uuid4()),
            "id": "12345",
            "status": "IN_PROGRESS",
            "previous_status": "LEAD",
            "sl": {"code": "en", "label": "English"},
            "tl": [{"code": "fr", "label": "French"}],
        }
        event = RayEvent(
            event="ray:job:status_changed",
            data={"client_id": mock_slack_user.ray_client_id, **status_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch("app.routers.ray.is_verify_job", return_value=False):
                            auth = RayEventAuth()
                            await auth.initialize(event, "valid-token")

                            # Should not raise exception
                            await ray_events(event, auth)

    @pytest.mark.asyncio
    async def test_ray_events_job_quote_created(
        self, mock_slack_user, user_id, team_id
    ):
        """Test job quote created event."""
        quote_data = {
            "client_id": str(uuid4()),
            "uuid": str(uuid4()),
            "id": "12345",
            "client_reference": "REF123",
            "status": "LEAD",
            "sl": {"code": "en", "label": "English"},
            "tl": [{"code": "fr", "label": "French"}],
            "service": "translation",
            "turnaround_days": 2.0,
            "quote": {
                "currency": "USD",
                "quote": 100.0,
                "quote_nett": 90.0,
                "quote_detail_url": "https://example.com/quote",
                "quote_accept_url": "https://example.com/accept",
                "quote_cancel_url": "https://example.com/cancel",
                "tl": {"fr": {"price": 100.0}},
            },
        }
        event = RayEvent(
            event="ray:job:quote_created",
            data={"client_id": mock_slack_user.ray_client_id, **quote_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.post_notification", new_callable=AsyncMock
                        ) as mock_post:
                            auth = RayEventAuth()
                            await auth.initialize(event, "valid-token")

                            await ray_events(event, auth)

                            # Verify notification was sent
                            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_no_slack_user(self):
        """Test event handling when no slack user is found."""
        event = RayEvent(
            event="ray:job:status_changed",
            data={"client_id": "nonexistent"},
        )

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=None):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    auth = RayEventAuth()
                    await auth.initialize(event, "valid-token")

                    # Should not raise exception, just skip handling
                    await ray_events(event, auth)
                    assert auth.slack_user is None

    @pytest.mark.asyncio
    async def test_ray_events_job_quote_accepted(
        self, mock_slack_user, user_id, team_id
    ):
        """Test job quote accepted event."""
        quote_accepted_data = {
            "client_id": str(uuid4()),
            "uuid": str(uuid4()),
            "id": "12345",
            "target_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        event = RayEvent(
            event="ray:job:quote_accepted",
            data={"client_id": mock_slack_user.ray_client_id, **quote_accepted_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.post_notification", new_callable=AsyncMock
                        ) as mock_post:
                            auth = RayEventAuth()
                            await auth.initialize(event, "valid-token")

                            await ray_events(event, auth)

                            # Verify notification was sent
                            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_job_quote_cancelled(
        self, mock_slack_user, user_id, team_id
    ):
        """Test job quote cancelled event."""
        quote_cancelled_data = {
            "client_id": str(uuid4()),
            "uuid": str(uuid4()),
            "id": "12345",
            "target_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        event = RayEvent(
            event="ray:job:quote_cancelled",
            data={"client_id": mock_slack_user.ray_client_id, **quote_cancelled_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.post_notification", new_callable=AsyncMock
                        ) as mock_post:
                            auth = RayEventAuth()
                            await auth.initialize(event, "valid-token")

                            await ray_events(event, auth)

                            # Verify notification was sent
                            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_transcription_success(
        self, mock_slack_user, user_id, team_id
    ):
        """Test transcription success event."""
        transcription_data = {
            "client_id": str(uuid4()),
            "task_uuid": str(uuid4()),
            "file_id": str(uuid4()),
            "file_name": "transcription.txt",
            "source_file_name": "video.mp4",
            "tokens": 100,
            "error": None,
        }
        event = RayEvent(
            event="transcription:slack:media:transcription:results",
            data={"client_id": mock_slack_user.ray_client_id, **transcription_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }
        mock_response = MagicMock()
        mock_response.data = {"channel": "C123"}

        # Create mock task info
        mock_task_info = TranscriptionTaskInfo(
            task_uuid=transcription_data["task_uuid"],
            client_id=mock_slack_user.ray_client_id,
            file_name="video.mp4",
            download_url="https://example.com/video.mp4",
            bot_token="xoxb-test-token",
            pipeline_type="transcribe",
            status="completed",
            stage=None,
            error_message=None,
            result_file_id=transcription_data["file_id"],
            result_file_name=transcription_data["file_name"],
            detected_language=None,
            translated_file_ids=None,
            extra_data=None,
            started_at=None,
            finished_at=None,
            duration_ms=None,
            source_text_length=None,
            num_target_languages=None,
            tokens_consumed=0,
            credit_transaction_uuid=None,
            model=None,
            service=None,
            app_source=None,
            created_at=datetime.datetime.now(),
            updated_at=datetime.datetime.now(),
        )

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.transcriber_tasks.tasks.get_transcription_task",
                            return_value=mock_task_info,
                        ):
                            with patch(
                                "app.routers.ray.post_notification",
                                new_callable=AsyncMock,
                                return_value=mock_response,
                            ) as mock_post:
                                with patch(
                                    "app.routers.ray._create_background_task"
                                ) as mock_bg_task:
                                    auth = RayEventAuth()
                                    await auth.initialize(event, "valid-token")

                                    await ray_events(event, auth)

                                    # Verify notification was sent and background task created
                                    mock_post.assert_called_once()
                                    mock_bg_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_transcription_error(
        self, mock_slack_user, user_id, team_id
    ):
        """Test transcription error event."""
        transcription_data = {
            "client_id": str(uuid4()),
            "task_uuid": str(uuid4()),
            "file_id": str(uuid4()),
            "file_name": "transcription.txt",
            "source_file_name": "video.mp4",
            "tokens": 0,
            "error": "No sound",
        }
        event = RayEvent(
            event="transcription:slack:media:transcription:results",
            data={"client_id": mock_slack_user.ray_client_id, **transcription_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }
        mock_client.chat_postEphemeral = AsyncMock()

        # Create mock task info with error
        mock_task_info = TranscriptionTaskInfo(
            task_uuid=transcription_data["task_uuid"],
            client_id=mock_slack_user.ray_client_id,
            file_name="video.mp4",
            download_url="https://example.com/video.mp4",
            bot_token="xoxb-test-token",
            pipeline_type="transcribe",
            status="failed",
            stage=None,
            error_message=transcription_data["error"],
            result_file_id=None,
            result_file_name=None,
            detected_language=None,
            translated_file_ids=None,
            extra_data=None,
            started_at=None,
            finished_at=None,
            duration_ms=None,
            source_text_length=None,
            num_target_languages=None,
            tokens_consumed=0,
            credit_transaction_uuid=None,
            model=None,
            service=None,
            app_source=None,
            created_at=datetime.datetime.now(),
            updated_at=datetime.datetime.now(),
        )

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.transcriber_tasks.tasks.get_transcription_task",
                            return_value=mock_task_info,
                        ):
                            auth = RayEventAuth()
                            await auth.initialize(event, "valid-token")

                            await ray_events(event, auth)

                            # Verify error message was sent
                            mock_client.chat_postEphemeral.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_document_translated_error(
        self, mock_slack_user, user_id, team_id
    ):
        """Test document translated error event."""
        error_data = {
            "error": True,
            "client_id": str(uuid4()),
            "channel_id": "C123",
            "error_type": "insufficient_balance",
            "error_data": {"balance": 10, "required": 100},
            "submission_id": 123,
        }
        event = RayEvent(
            event="verify:slack:document:translated",
            data={"client_id": mock_slack_user.ray_client_id, **error_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.get_client_type", return_value="Admin"
                        ):
                            with patch(
                                "app.routers.ray.post_notification_ephemeral",
                                new_callable=AsyncMock,
                            ) as mock_post:
                                with patch("app.routers.ray.updated_submission_status"):
                                    auth = RayEventAuth()
                                    await auth.initialize(event, "valid-token")

                                    await ray_events(event, auth)

                                    # Verify error notification was sent
                                    mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_document_translated_success(
        self, mock_slack_user, user_id, team_id
    ):
        """Test document translated success event."""
        success_data = {
            "task_uuid": str(uuid4()),
            "file_id": str(uuid4()),
            "tokens": 100,
            "client_id": str(uuid4()),
            "target_language": "fr",
            "channel_id": "C123",
            "submission_id": 123,
        }
        event = RayEvent(
            event="verify:slack:document:translated",
            data={"client_id": mock_slack_user.ray_client_id, **success_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray._create_background_task"
                        ) as mock_bg_task:
                            auth = RayEventAuth()
                            await auth.initialize(event, "valid-token")

                            await ray_events(event, auth)

                            # Verify background task was created
                            mock_bg_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_evaluate_complete_error(
        self, mock_slack_user, user_id, team_id
    ):
        """Test evaluate complete error event."""
        event_data = {
            "error": True,
            "job_uuid": str(uuid4()),
        }
        event = RayEvent(
            event="verify:slack:evaluate:complete",
            data={"client_id": mock_slack_user.ray_client_id, **event_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.post_notification", new_callable=AsyncMock
                        ) as mock_post:
                            auth = RayEventAuth()
                            await auth.initialize(event, "valid-token")

                            await ray_events(event, auth)

                            # Verify error notification was sent
                            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_evaluate_complete_success(
        self, mock_slack_user, user_id, team_id
    ):
        """Test evaluate complete success event."""
        event_data = {
            "error": False,
            "job_uuid": str(uuid4()),
            "tokens": 50,
        }
        lang_uuid = str(uuid4())
        mock_job = {
            "data": {
                "uuid": event_data["job_uuid"],
                "workflow_uuid": str(uuid4()),  # Not human evaluation workflow
                "human_job_in_progress": False,
                "source_files": [
                    {
                        "file_uuid": str(uuid4()),
                        "filename": "test.pdf",
                        "target_files": [
                            {
                                "language_uuid": lang_uuid,
                                "human_job_status": None,
                                "target_file_uuid": str(uuid4()),
                            }
                        ],
                        "report": {
                            "language_uuid": str(uuid4()),
                            "evaluation_reports": [
                                {
                                    "target_language": lang_uuid,
                                    "score": 95.5,
                                    "count": {
                                        "bad": 0,
                                        "best": 10,
                                        "good": 5,
                                        "no_score": 0,
                                        "acceptable": 2,
                                        "un_translated": 0,
                                        "translation_memory": 0,
                                    },
                                }
                            ],
                        },
                    }
                ],
                "target_languages": [{"uuid": lang_uuid, "name": "French"}],
            }
        }

        event = RayEvent(
            event="verify:slack:evaluate:complete",
            data={"client_id": mock_slack_user.ray_client_id, **event_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.get_evaluation_job",
                            return_value=mock_job,
                        ):
                            with patch(
                                "app.routers.ray._get_languages_cached",
                                return_value=[],
                            ):
                                with patch(
                                    "app.routers.ray.post_notification",
                                    new_callable=AsyncMock,
                                ) as mock_post:
                                    auth = RayEventAuth()
                                    await auth.initialize(event, "valid-token")

                                    await ray_events(event, auth)

                                    # Verify success notification was sent
                                    mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_human_verification_completed(
        self, mock_slack_user, user_id, team_id
    ):
        """Test human verification completed event."""
        event_data = {
            "job_title": "Test Job",
            "lang_uuid": str(uuid4()),
            "grid_file_id": str(uuid4()),
        }
        mock_languages = [{"uuid": event_data["lang_uuid"], "name": "French"}]

        event = RayEvent(
            event="verify:human_verification:completed",
            data={"client_id": mock_slack_user.ray_client_id, **event_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }
        mock_response = MagicMock()
        mock_response.data = {"channel": "C123"}

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray._get_languages_cached",
                            return_value=mock_languages,
                        ):
                            with patch(
                                "app.routers.ray.post_notification",
                                new_callable=AsyncMock,
                                return_value=mock_response,
                            ) as mock_post:
                                with patch(
                                    "app.routers.ray._create_background_task"
                                ) as mock_bg_task:
                                    auth = RayEventAuth()
                                    await auth.initialize(event, "valid-token")

                                    await ray_events(event, auth)

                                    # Verify notification was sent and background task created
                                    mock_post.assert_called_once()
                                    mock_bg_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_direct_mt_result(self, mock_slack_user, user_id, team_id):
        """Test direct MT result event."""
        extra_data = {
            "client_id": str(uuid4()),
            "service_language_mapping": {"service1": ["fr"]},
            "source_language": "en",
            "organization_uuid": str(uuid4()),
            "channel_id": "C123",
            "text_length": 100,
            "usage_type": "direct_machine_translation",
            "source_text": "Hello world",
            "response_url": None,
            "thread_ts": None,
            "is_edit": False,
        }
        event_data = {
            "extra_data": extra_data,
            "translations": {"fr": ["Bonjour le monde"]},
        }
        event = RayEvent(
            event="slack:direct:mt:result",
            data={"client_id": mock_slack_user.ray_client_id, **event_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }
        mock_client.conversations_info.return_value = {
            "channel": {"name": "test-channel"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.post_notification", new_callable=AsyncMock
                        ) as mock_post:
                            with patch(
                                "app.routers.ray.calculate_cost", return_value=1.0
                            ):
                                with patch(
                                    "app.routers.ray.spend_credits",
                                    new_callable=AsyncMock,
                                ) as mock_spend:
                                    mock_spend.return_value = str(uuid4())
                                    with patch(
                                        "app.routers.ray.log_google_api_usage",
                                        new_callable=AsyncMock,
                                    ):
                                        auth = RayEventAuth()
                                        await auth.initialize(event, "valid-token")

                                        await ray_events(event, auth)

                                        # Verify notification was sent
                                        mock_post.assert_called_once()
                                        mock_spend.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_channel_translation_result(
        self, mock_slack_user, user_id, team_id
    ):
        """Test channel translation result event."""
        extra_data = {
            "client_id": str(uuid4()),
            "service_language_mapping": {"service1": ["fr", "es"]},
            "source_language": "en",
            "organization_uuid": str(uuid4()),
            "channel_id": "C123",
            "text_length": 100,
            "usage_type": "channel_translation",
            "source_text": "Hello world",
            "display_format": "thread",
            "message_ts": "123456.789",
        }
        event_data = {
            "extra_data": extra_data,
            "translations": {"fr": ["Bonjour"], "es": ["Hola"]},
        }
        event = RayEvent(
            event="slack:direct:mt:result",
            data={"client_id": mock_slack_user.ray_client_id, **event_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }
        mock_client.conversations_info.return_value = {
            "channel": {"name": "test-channel"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.post_channel_translation_notification",
                            new_callable=AsyncMock,
                        ) as mock_post:
                            with patch(
                                "app.routers.ray.calculate_cost", return_value=2.0
                            ):
                                with patch(
                                    "app.routers.ray.spend_credits",
                                    new_callable=AsyncMock,
                                ) as mock_spend:
                                    mock_spend.return_value = str(uuid4())
                                    with patch(
                                        "app.routers.ray.log_google_api_usage",
                                        new_callable=AsyncMock,
                                    ):
                                        auth = RayEventAuth()
                                        await auth.initialize(event, "valid-token")

                                        await ray_events(event, auth)

                                        # Verify channel translation notification was sent
                                        mock_post.assert_called_once()
                                        mock_spend.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_invalid_event_type(self, mock_slack_user):
        """Test invalid event type raises 400."""
        event = RayEvent(
            event="invalid:event:type",
            data={"client_id": mock_slack_user.ray_client_id},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {"user": {"id": "U123"}}

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch("app.dependencies.get_slack_user", return_value=mock_slack_user):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        auth = RayEventAuth()
                        await auth.initialize(event, "valid-token")

                        # Should raise HTTPException with 400 for invalid event type
                        with pytest.raises(HTTPException) as exc_info:
                            await ray_events(event, auth)
                        assert exc_info.value.status_code == 400


class TestRayCallbackEndpoint:
    """Tests for /ray/callback endpoint."""

    @pytest.mark.asyncio
    async def test_api_job_callback_no_slack_user(self, user_id):
        """Test callback when slack user is not found."""
        callback_data = RayCallback(
            event_types=["JOB_NUMBER"],
            job=[{"tj_number": "TJ12345"}],
        )
        mock_request = MagicMock(spec=Request)
        mock_request.body = AsyncMock(return_value=b'{"event_types": ["JOB_NUMBER"]}')

        with patch("app.routers.ray.get_slack_user", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await api_job_callback(
                    mock_request,
                    client_id="nonexistent",
                    body=callback_data,
                    x_straker_signature="signature",
                )
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_api_job_callback_invalid_signature(self, mock_slack_user):
        """Test callback with invalid signature."""
        callback_data = RayCallback(
            event_types=["JOB_NUMBER"],
            job=[{"tj_number": "TJ12345"}],
        )
        mock_request = MagicMock(spec=Request)
        mock_request.body = AsyncMock(return_value=b'{"event_types": ["JOB_NUMBER"]}')

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": mock_slack_user.user_id, "locale": "en-US"}
        }

        with patch("app.routers.ray.get_slack_user", return_value=mock_slack_user):
            with patch("app.routers.ray.get_demo_link", return_value=[]):
                with patch("app.routers.ray.AsyncWebClient", return_value=mock_client):
                    with patch(
                        "app.routers.ray.get_client_access_tokens",
                        return_value=["token1"],
                    ):
                        with patch(
                            "app.routers.ray.validate_api_callback_signature",
                            return_value=False,
                        ):
                            with pytest.raises(HTTPException) as exc_info:
                                await api_job_callback(
                                    mock_request,
                                    client_id=mock_slack_user.ray_client_id,
                                    body=callback_data,
                                    x_straker_signature="invalid-signature",
                                )
                            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_api_job_callback_job_number_success(self, mock_slack_user):
        """Test successful job number callback."""
        callback_data = RayCallback(
            event_types=["JOB_NUMBER"],
            job=[{"tj_number": "TJ12345"}],
        )
        mock_request = MagicMock(spec=Request)
        mock_request.body = AsyncMock(return_value=b'{"event_types": ["JOB_NUMBER"]}')

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": mock_slack_user.user_id, "locale": "en-US"}
        }
        mock_client.chat_postMessage = AsyncMock()

        with patch("app.routers.ray.get_slack_user", return_value=mock_slack_user):
            with patch("app.routers.ray.get_demo_link", return_value=[]):
                with patch("app.routers.ray.AsyncWebClient", return_value=mock_client):
                    with patch(
                        "app.routers.ray.get_client_access_tokens",
                        return_value=["token1"],
                    ):
                        with patch(
                            "app.routers.ray.validate_api_callback_signature",
                            return_value=True,
                        ):
                            with patch(
                                "app.routers.ray.is_ibm_enterprise", return_value=False
                            ):
                                with patch(
                                    "app.routers.ray.get_job_group_quote_settings",
                                    return_value=True,
                                ):
                                    result = await api_job_callback(
                                        mock_request,
                                        client_id=mock_slack_user.ray_client_id,
                                        body=callback_data,
                                        x_straker_signature="valid-signature",
                                    )

                                    assert result["message"] == "success"
                                    assert "JOB_NUMBER" in result["detail"]
                                    mock_client.chat_postMessage.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_job_callback_unhandled_event(self, mock_slack_user):
        """Test callback with unhandled event type."""
        callback_data = RayCallback(
            event_types=["UNKNOWN_EVENT"],
            job=[],
        )
        mock_request = MagicMock(spec=Request)
        mock_request.body = AsyncMock(
            return_value=b'{"event_types": ["UNKNOWN_EVENT"]}'
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": mock_slack_user.user_id, "locale": "en-US"}
        }

        with patch("app.routers.ray.get_slack_user", return_value=mock_slack_user):
            with patch("app.routers.ray.get_demo_link", return_value=[]):
                with patch("app.routers.ray.AsyncWebClient", return_value=mock_client):
                    with patch(
                        "app.routers.ray.get_client_access_tokens",
                        return_value=["token1"],
                    ):
                        with patch(
                            "app.routers.ray.validate_api_callback_signature",
                            return_value=True,
                        ):
                            result = await api_job_callback(
                                mock_request,
                                client_id=mock_slack_user.ray_client_id,
                                body=callback_data,
                                x_straker_signature="valid-signature",
                            )

                            assert result["message"] == "success"
                            assert "Unhandled callback event" in result["detail"]

    @pytest.mark.asyncio
    async def test_api_job_callback_invalid_payload(self, mock_slack_user):
        """Test callback with invalid payload format."""
        callback_data = RayCallback(
            event_types=["JOB_NUMBER"],
            job=[],  # Missing tj_number
        )
        mock_request = MagicMock(spec=Request)
        mock_request.body = AsyncMock(return_value=b'{"event_types": ["JOB_NUMBER"]}')

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": mock_slack_user.user_id, "locale": "en-US"}
        }

        with patch("app.routers.ray.get_slack_user", return_value=mock_slack_user):
            with patch("app.routers.ray.get_demo_link", return_value=[]):
                with patch("app.routers.ray.AsyncWebClient", return_value=mock_client):
                    with patch(
                        "app.routers.ray.get_client_access_tokens",
                        return_value=["token1"],
                    ):
                        with patch(
                            "app.routers.ray.validate_api_callback_signature",
                            return_value=True,
                        ):
                            with pytest.raises(HTTPException) as exc_info:
                                await api_job_callback(
                                    mock_request,
                                    client_id=mock_slack_user.ray_client_id,
                                    body=callback_data,
                                    x_straker_signature="valid-signature",
                                )
                            assert exc_info.value.status_code == 422
