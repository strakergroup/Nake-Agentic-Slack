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
from app.ray.events.evaluate_quote_events import (
    RAY_EVENT_DEDUPE_TTL_SECONDS,
    claim_ray_event_notification,
)
from app.ray.events.models import (
    ClientGroup,
)
from app.routers.ray import RayCallback, api_job_callback, ray_events, router
from app.slack.utils import format_callback_error


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

    @pytest.mark.parametrize(
        ("stage", "expected"),
        [
            ("transcription", "Transcription failed: No sound"),
            ("translation", "Translation failed: MT service unavailable"),
            ("embedding", "Embedding failed: Video codec unsupported"),
        ],
    )
    def test_callback_error_text_uses_exportable_literal_template(
        self, stage, expected
    ):
        payload_error = expected.split(": ", 1)[1]

        assert format_callback_error(stage, payload_error) == expected

    def test_callback_error_text_uses_exportable_unknown_error_template(self):
        assert format_callback_error("translation", "") == (
            "Translation failed: Unknown error"
        )

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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
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
            with patch(
                "app.dependencies.resolve_slack_delivery_user", return_value=None
            ):
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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.get_transcription_task",
                            new_callable=AsyncMock,
                            return_value=mock_task_info,
                        ):
                            with patch(
                                "app.routers.ray.mark_stage_processed",
                                new_callable=AsyncMock,
                            ):
                                with patch(
                                    "app.routers.ray.spend_transcription_credits",
                                    new_callable=AsyncMock,
                                    return_value=0,
                                ):
                                    with patch(
                                        "app.routers.ray.update_tokens_consumed",
                                        new_callable=AsyncMock,
                                    ):
                                        with patch(
                                            "app.routers.ray.handle_transcription_complete",
                                            new_callable=AsyncMock,
                                        ) as mock_handle:
                                            auth = RayEventAuth()
                                            await auth.initialize(event, "valid-token")

                                            await ray_events(event, auth)

                                            mock_handle.assert_awaited_once()

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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.get_transcription_task",
                            new_callable=AsyncMock,
                            return_value=mock_task_info,
                        ):
                            with patch(
                                "app.routers.ray.fail_media_submissions",
                                new_callable=AsyncMock,
                            ) as mock_fail:
                                auth = RayEventAuth()
                                await auth.initialize(event, "valid-token")

                                await ray_events(event, auth)

                                mock_client.chat_postEphemeral.assert_called_once()
                                assert (
                                    mock_client.chat_postEphemeral.call_args.kwargs[
                                        "text"
                                    ]
                                    == "Transcription failed: No sound"
                                )
                                mock_fail.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ray_events_translation_error_fails_submissions(
        self, mock_slack_user, user_id, team_id
    ):
        """Translation callback errors mark media submissions failed."""
        task_uuid = str(uuid4())
        event = RayEvent(
            event="transcription:slack:media:translation:results",
            data={
                "client_id": mock_slack_user.ray_client_id,
                "task_uuid": task_uuid,
                "error": "MT failed",
            },
        )
        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }
        mock_client.chat_postEphemeral = AsyncMock()
        mock_task_info = TranscriptionTaskInfo(
            task_uuid=task_uuid,
            client_id=mock_slack_user.ray_client_id,
            file_name="clip.srt",
            download_url="https://example.com/clip.srt",
            bot_token="xoxb-test-token",
            pipeline_type="translate_only",
            status="failed",
            stage=None,
            error_message="MT failed",
            result_file_id=None,
            result_file_name=None,
            detected_language=None,
            translated_file_ids=None,
            extra_data={"submission_id": 99},
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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.get_transcription_task",
                            new_callable=AsyncMock,
                            return_value=mock_task_info,
                        ):
                            with patch(
                                "app.routers.ray.fail_media_submissions",
                                new_callable=AsyncMock,
                            ) as mock_fail:
                                auth = RayEventAuth()
                                await auth.initialize(event, "valid-token")
                                await ray_events(event, auth)
                                mock_fail.assert_awaited_once_with(
                                    {"submission_id": 99}
                                )

    @pytest.mark.asyncio
    async def test_ray_events_translation_empty_ids_fails_submissions(
        self, mock_slack_user, user_id, team_id
    ):
        """Empty translated_file_ids fails submissions instead of silent skip."""
        task_uuid = str(uuid4())
        event = RayEvent(
            event="transcription:slack:media:translation:results",
            data={
                "client_id": mock_slack_user.ray_client_id,
                "task_uuid": task_uuid,
                "error": None,
            },
        )
        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }
        mock_client.chat_postEphemeral = AsyncMock()
        mock_task_info = TranscriptionTaskInfo(
            task_uuid=task_uuid,
            client_id=mock_slack_user.ray_client_id,
            file_name="clip.srt",
            download_url="https://example.com/clip.srt",
            bot_token="xoxb-test-token",
            pipeline_type="translate_only",
            status="completed",
            stage=None,
            error_message=None,
            result_file_id=None,
            result_file_name=None,
            detected_language=None,
            translated_file_ids={},
            extra_data={"submission_ids": [42, 43]},
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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.get_transcription_task",
                            new_callable=AsyncMock,
                            return_value=mock_task_info,
                        ):
                            with patch(
                                "app.routers.ray.fail_media_submissions",
                                new_callable=AsyncMock,
                            ) as mock_fail:
                                auth = RayEventAuth()
                                await auth.initialize(event, "valid-token")
                                await ray_events(event, auth)
                                mock_client.chat_postEphemeral.assert_called_once()
                                assert (
                                    "no output files"
                                    in (
                                        mock_client.chat_postEphemeral.call_args.kwargs[
                                            "text"
                                        ]
                                    )
                                )
                                mock_fail.assert_awaited_once()

    def _partial_translation_task_info(self, task_uuid, client_id):
        return TranscriptionTaskInfo(
            task_uuid=task_uuid,
            client_id=client_id,
            file_name="clip.srt",
            download_url="https://example.com/clip.srt",
            bot_token="xoxb-test-token",
            pipeline_type="translate_only",
            status="completed",
            stage=None,
            error_message=None,
            result_file_id=None,
            result_file_name=None,
            detected_language=None,
            translated_file_ids={"es": "file-es"},
            extra_data={"submission_ids": [42, 43], "slack_channel_id": "C1"},
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

    @pytest.mark.asyncio
    async def test_ray_events_translation_partial_does_not_fail_submissions(
        self, mock_slack_user, user_id, team_id
    ):
        """A partial result delivers what arrived and must not fail those submissions."""
        task_uuid = str(uuid4())
        event = RayEvent(
            event="transcription:slack:media:translation:results",
            data={
                "client_id": mock_slack_user.ray_client_id,
                "task_uuid": task_uuid,
                "error": None,
                "failed_languages": ["fr"],
            },
        )
        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }
        mock_task_info = self._partial_translation_task_info(
            task_uuid, mock_slack_user.ray_client_id
        )

        with (
            patch("app.dependencies.validate_queue_proxy_secret", return_value=True),
            patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ),
            patch("app.dependencies.get_demo_link", return_value=[]),
            patch("app.routers.ray.AsyncWebClient", return_value=mock_client),
            patch(
                "app.routers.ray.get_transcription_task",
                new_callable=AsyncMock,
                return_value=mock_task_info,
            ),
            patch("app.routers.ray.mark_stage_processed", new_callable=AsyncMock),
            patch(
                "app.routers.ray.handle_translation_complete",
                new_callable=AsyncMock,
                return_value=1,
            ) as mock_handle,
            patch(
                "app.routers.ray.spend_translation_credits",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "app.routers.ray.update_submission_status", new_callable=AsyncMock
            ) as mock_complete,
            patch("app.routers.ray.mark_media_quote_done", new_callable=AsyncMock),
            patch(
                "app.routers.ray.fail_media_submissions", new_callable=AsyncMock
            ) as mock_fail,
        ):
            auth = RayEventAuth()
            await auth.initialize(event, "valid-token")
            await ray_events(event, auth)

        assert mock_handle.await_args.kwargs["failed_languages"] == ["fr"]
        mock_fail.assert_not_awaited()
        mock_complete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ray_events_translation_without_failed_languages_passes_none(
        self, mock_slack_user, user_id, team_id
    ):
        """An older sup-subtitle payload omits the field and behaves as before."""
        task_uuid = str(uuid4())
        event = RayEvent(
            event="transcription:slack:media:translation:results",
            data={
                "client_id": mock_slack_user.ray_client_id,
                "task_uuid": task_uuid,
                "error": None,
            },
        )
        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }
        mock_task_info = self._partial_translation_task_info(
            task_uuid, mock_slack_user.ray_client_id
        )

        with (
            patch("app.dependencies.validate_queue_proxy_secret", return_value=True),
            patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ),
            patch("app.dependencies.get_demo_link", return_value=[]),
            patch("app.routers.ray.AsyncWebClient", return_value=mock_client),
            patch(
                "app.routers.ray.get_transcription_task",
                new_callable=AsyncMock,
                return_value=mock_task_info,
            ),
            patch("app.routers.ray.mark_stage_processed", new_callable=AsyncMock),
            patch(
                "app.routers.ray.handle_translation_complete",
                new_callable=AsyncMock,
                return_value=1,
            ) as mock_handle,
            patch(
                "app.routers.ray.spend_translation_credits",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch("app.routers.ray.update_submission_status", new_callable=AsyncMock),
            patch("app.routers.ray.mark_media_quote_done", new_callable=AsyncMock),
            patch(
                "app.routers.ray.fail_media_submissions", new_callable=AsyncMock
            ) as mock_fail,
        ):
            auth = RayEventAuth()
            await auth.initialize(event, "valid-token")
            await ray_events(event, auth)

        assert mock_handle.await_args.kwargs["failed_languages"] is None
        mock_fail.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ray_events_configure_translation_does_not_mark_quote_done(
        self, mock_slack_user, user_id, team_id
    ):
        """Configure review gate owns completion; Quote 2 translate_only must not mark done."""
        task_uuid = str(uuid4())
        event = RayEvent(
            event="transcription:slack:media:translation:results",
            data={
                "client_id": mock_slack_user.ray_client_id,
                "task_uuid": task_uuid,
                "error": None,
            },
        )
        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }
        mock_task_info = self._partial_translation_task_info(
            task_uuid, mock_slack_user.ray_client_id
        )
        mock_task_info.extra_data["workflow_type"] = "transcribe_translate"
        mock_task_info.extra_data["media_quote_id"] = "q1"

        with (
            patch("app.dependencies.validate_queue_proxy_secret", return_value=True),
            patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ),
            patch("app.dependencies.get_demo_link", return_value=[]),
            patch("app.routers.ray.AsyncWebClient", return_value=mock_client),
            patch(
                "app.routers.ray.get_transcription_task",
                new_callable=AsyncMock,
                return_value=mock_task_info,
            ),
            patch("app.routers.ray.mark_stage_processed", new_callable=AsyncMock),
            patch(
                "app.routers.ray.handle_translation_complete",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "app.routers.ray.spend_translation_credits",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "app.routers.ray.update_submission_status", new_callable=AsyncMock
            ) as mock_complete,
            patch(
                "app.routers.ray.mark_media_quote_done", new_callable=AsyncMock
            ) as mock_done,
            patch("app.routers.ray.fail_media_submissions", new_callable=AsyncMock),
        ):
            auth = RayEventAuth()
            await auth.initialize(event, "valid-token")
            await ray_events(event, auth)

        mock_complete.assert_not_awaited()
        mock_done.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ray_events_configure_source_embed_does_not_mark_quote_done(
        self, mock_slack_user, user_id, team_id
    ):
        """Source embed must not close the session while Quote 2 is still pending."""
        task_uuid = str(uuid4())
        event = RayEvent(
            event="transcription:slack:media:embedding:results",
            data={
                "client_id": mock_slack_user.ray_client_id,
                "task_uuid": task_uuid,
                "error": None,
            },
        )
        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }
        mock_task_info = TranscriptionTaskInfo(
            task_uuid=task_uuid,
            client_id=mock_slack_user.ray_client_id,
            file_name="clip.mp4",
            download_url="https://example.com/clip.mp4",
            bot_token="xoxb-test-token",
            pipeline_type="embed",
            status="completed",
            stage=None,
            error_message=None,
            result_file_id="file-embedded",
            result_file_name="clip_embedded.mp4",
            detected_language=None,
            translated_file_ids=None,
            extra_data={
                "workflow_type": "transcribe_translate",
                "media_quote_id": "q1",
                "submission_ids": [42],
                "slack_channel_id": "C1",
            },
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

        with (
            patch("app.dependencies.validate_queue_proxy_secret", return_value=True),
            patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ),
            patch("app.dependencies.get_demo_link", return_value=[]),
            patch("app.routers.ray.AsyncWebClient", return_value=mock_client),
            patch(
                "app.routers.ray.get_transcription_task",
                new_callable=AsyncMock,
                return_value=mock_task_info,
            ),
            patch("app.routers.ray.mark_stage_processed", new_callable=AsyncMock),
            patch(
                "app.routers.ray.handle_transcribe_embed_pipeline",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.routers.ray.spend_embedding_credits",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "app.routers.ray.update_submission_status", new_callable=AsyncMock
            ) as mock_complete,
            patch(
                "app.routers.ray.mark_media_quote_done", new_callable=AsyncMock
            ) as mock_done,
            patch("app.routers.ray.fail_media_submissions", new_callable=AsyncMock),
        ):
            auth = RayEventAuth()
            await auth.initialize(event, "valid-token")
            await ray_events(event, auth)

        mock_complete.assert_not_awaited()
        mock_done.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ray_events_configure_source_embed_error_does_not_fail_submissions(
        self, mock_slack_user, user_id, team_id
    ):
        task_uuid = str(uuid4())
        event = RayEvent(
            event="transcription:slack:media:embedding:results",
            data={
                "client_id": mock_slack_user.ray_client_id,
                "task_uuid": task_uuid,
                "error": "burn-in failed",
            },
        )
        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }
        mock_task_info = TranscriptionTaskInfo(
            task_uuid=task_uuid,
            client_id=mock_slack_user.ray_client_id,
            file_name="clip.mp4",
            download_url="https://example.com/clip.mp4",
            bot_token="xoxb-test-token",
            pipeline_type="embed",
            status="failed",
            stage=None,
            error_message="burn-in failed",
            result_file_id=None,
            result_file_name=None,
            detected_language=None,
            translated_file_ids=None,
            extra_data={
                "workflow_type": "transcribe_translate",
                "media_quote_id": "q1",
                "embed_role": "source",
                "submission_ids": [42, 43],
                "slack_channel_id": "C1",
                "slack_user_id": user_id,
                "slack_thread_ts": "123.456",
            },
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

        with (
            patch("app.dependencies.validate_queue_proxy_secret", return_value=True),
            patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ),
            patch("app.dependencies.get_demo_link", return_value=[]),
            patch("app.routers.ray.AsyncWebClient", return_value=mock_client),
            patch(
                "app.routers.ray.get_transcription_task",
                new_callable=AsyncMock,
                return_value=mock_task_info,
            ),
            patch(
                "app.routers.ray.fail_media_submissions", new_callable=AsyncMock
            ) as mock_fail,
        ):
            auth = RayEventAuth()
            await auth.initialize(event, "valid-token")
            await ray_events(event, auth)

        mock_fail.assert_not_awaited()
        posted = mock_client.chat_postMessage.await_args.kwargs
        assert posted["channel"] == "C1"
        assert posted["thread_ts"] == "123.456"
        assert "embed" in posted["text"].lower() or "subtitl" in posted["text"].lower()

    @pytest.mark.asyncio
    async def test_ray_events_configure_source_embed_error_fails_transcribe_only_submissions(
        self, mock_slack_user, user_id, team_id
    ):
        task_uuid = str(uuid4())
        event = RayEvent(
            event="transcription:slack:media:embedding:results",
            data={
                "client_id": mock_slack_user.ray_client_id,
                "task_uuid": task_uuid,
                "error": "burn-in failed",
            },
        )
        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }
        mock_task_info = TranscriptionTaskInfo(
            task_uuid=task_uuid,
            client_id=mock_slack_user.ray_client_id,
            file_name="clip.mp4",
            download_url="https://example.com/clip.mp4",
            bot_token="xoxb-test-token",
            pipeline_type="embed",
            status="failed",
            stage=None,
            error_message="burn-in failed",
            result_file_id=None,
            result_file_name=None,
            detected_language=None,
            translated_file_ids=None,
            extra_data={
                "workflow_type": "transcribe_only",
                "media_quote_id": "q1",
                "embed_role": "source",
                "submission_ids": [42],
                "slack_channel_id": "C1",
                "slack_user_id": user_id,
                "slack_thread_ts": "123.456",
            },
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

        with (
            patch("app.dependencies.validate_queue_proxy_secret", return_value=True),
            patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ),
            patch("app.dependencies.get_demo_link", return_value=[]),
            patch("app.routers.ray.AsyncWebClient", return_value=mock_client),
            patch(
                "app.routers.ray.get_transcription_task",
                new_callable=AsyncMock,
                return_value=mock_task_info,
            ),
            patch(
                "app.routers.ray.fail_media_submissions", new_callable=AsyncMock
            ) as mock_fail,
            patch(
                "app.routers.ray.mark_media_quote_cancelled", new_callable=AsyncMock
            ) as mock_cancel,
        ):
            auth = RayEventAuth()
            await auth.initialize(event, "valid-token")
            await ray_events(event, auth)

        mock_fail.assert_awaited_once()
        mock_cancel.assert_awaited_once()
        posted = mock_client.chat_postMessage.await_args.kwargs
        assert "translation" not in posted["text"].lower()

    @pytest.mark.asyncio
    async def test_ray_events_configure_source_embed_delivery_failure_fails_transcribe_only_submissions(
        self, mock_slack_user, user_id, team_id
    ):
        """Transcribe-only source embed must fail submissions so 24h dedupe allows retry."""
        task_uuid = str(uuid4())
        event = RayEvent(
            event="transcription:slack:media:embedding:results",
            data={
                "client_id": mock_slack_user.ray_client_id,
                "task_uuid": task_uuid,
                "error": None,
            },
        )
        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }
        mock_task_info = TranscriptionTaskInfo(
            task_uuid=task_uuid,
            client_id=mock_slack_user.ray_client_id,
            file_name="clip.mp4",
            download_url="https://example.com/clip.mp4",
            bot_token="xoxb-test-token",
            pipeline_type="embed",
            status="completed",
            stage=None,
            error_message=None,
            result_file_id="file-embedded",
            result_file_name="clip_embedded.mp4",
            detected_language=None,
            translated_file_ids=None,
            extra_data={
                "workflow_type": "transcribe_only",
                "media_quote_id": "q1",
                "embed_role": "source",
                "submission_ids": [42],
                "slack_channel_id": "C1",
                "slack_user_id": user_id,
                "slack_thread_ts": "123.456",
            },
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

        with (
            patch("app.dependencies.validate_queue_proxy_secret", return_value=True),
            patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ),
            patch("app.dependencies.get_demo_link", return_value=[]),
            patch("app.routers.ray.AsyncWebClient", return_value=mock_client),
            patch(
                "app.routers.ray.get_transcription_task",
                new_callable=AsyncMock,
                return_value=mock_task_info,
            ),
            patch("app.routers.ray.mark_stage_processed", new_callable=AsyncMock),
            patch(
                "app.routers.ray.handle_transcribe_embed_pipeline",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "app.routers.ray.fail_media_submissions", new_callable=AsyncMock
            ) as mock_fail,
            patch(
                "app.routers.ray.mark_media_quote_cancelled", new_callable=AsyncMock
            ) as mock_cancel,
        ):
            auth = RayEventAuth()
            await auth.initialize(event, "valid-token")
            await ray_events(event, auth)

        mock_fail.assert_awaited_once()
        mock_cancel.assert_awaited_once()

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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
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
    async def test_ray_events_document_mt_quote_posts_quote_message(
        self, mock_slack_user, user_id, team_id
    ):
        quote_id = str(uuid4())
        event = RayEvent(
            event="verify:slack:document:quote",
            data={
                "quote_id": quote_id,
                "client_id": mock_slack_user.ray_client_id,
                "channel_id": "C123",
                "currency": "USD",
                "total_tokens": 10,
                "total_cost_usd": 0.02,
                "files": [
                    {
                        "file_id": "grid-1",
                        "file_name": "a.docx",
                        "character_count": 5000,
                        "target_languages": [
                            {
                                "target_language": "fr",
                                "tokens": 10,
                                "cost_usd": 0.02,
                            }
                        ],
                    }
                ],
            },
        )
        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }
        session = {
            "quote_id": quote_id,
            "status": "quoted",
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": "C123",
            "source_language": "en",
            "target_languages": ["fr"],
            "files": [],
            "quote": event.data,
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                new_callable=AsyncMock,
                return_value=mock_slack_user,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.apply_document_mt_quote_result",
                            new_callable=AsyncMock,
                            return_value=session,
                        ) as mock_apply:
                            with patch(
                                "app.routers.ray.post_notification",
                                new_callable=AsyncMock,
                            ) as mock_post:
                                auth = RayEventAuth()
                                await auth.initialize(event, "valid-token")

                                await ray_events(event, auth)

        mock_apply.assert_awaited_once()
        mock_post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ray_events_evaluate_pdf_quote_posts_evaluate_quote_message(
        self, mock_slack_user, user_id, team_id
    ):
        quote_id = str(uuid4())
        event = RayEvent(
            event="verify:slack:evaluate:pdf:quote",
            data={
                "quote_id": quote_id,
                "client_id": mock_slack_user.ray_client_id,
                "channel_id": "C123",
                "currency": "USD",
                "total_tokens": 26,
                "pdf_conversion_tokens": 25,
                "total_cost_usd": 0.52,
                "files": [
                    {
                        "file_id": "grid-1",
                        "file_name": "1Test.pdf",
                        "character_count": 15,
                        "pdf_conversion_page_count": 1,
                        "pdf_conversion_tokens": 25,
                        "target_languages": [
                            {
                                "target_language": "fr",
                                "tokens": 1,
                                "cost_usd": 0.02,
                            }
                        ],
                    }
                ],
            },
        )
        mock_client = AsyncMock()
        session = {
            "quote_id": quote_id,
            "channel_id": "C123",
            "enterprise_id": None,
            "ai_token_estimate": 1,
            "pdf_page_count": 1,
            "pdf_tokens": 25,
            "language_costs": [],
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                new_callable=AsyncMock,
                return_value=mock_slack_user,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.apply_pdf_evaluate_quote_result",
                            new_callable=AsyncMock,
                            return_value=session,
                        ) as mock_apply:
                            with patch(
                                "app.routers.ray.post_pdf_evaluate_quote_message",
                                new_callable=AsyncMock,
                            ) as mock_post:
                                auth = RayEventAuth()
                                await auth.initialize(event, "valid-token")

                                await ray_events(event, auth)

        mock_apply.assert_awaited_once()
        mock_post.assert_awaited_once()
        assert mock_post.await_args.kwargs["channel_id"] == "C123"

    @pytest.mark.asyncio
    async def test_ray_events_document_mt_quote_error_reaches_org_billed_poster(
        self, mock_slack_user, user_id, team_id
    ):
        """Org-billed quote errors must DM the Slack poster, not the org UUID.

        For an org-billed submission ``client_id`` is the Verify organization
        uuid, so the echoed ``team_id`` / ``slack_user_id`` are what let the
        error resolve to a real Slack member.
        """
        organization_uuid = str(uuid4())
        event = RayEvent(
            event="verify:slack:document:quote",
            data={
                "quote_id": str(uuid4()),
                "client_id": organization_uuid,
                "channel_id": "C123",
                "error": True,
                "error_type": "insufficient_balance",
                "error_data": {"balance": 0, "required": 10},
                "team_id": team_id,
                "slack_user_id": user_id,
            },
        )
        mock_client = AsyncMock()

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                new_callable=AsyncMock,
                return_value=mock_slack_user,
            ) as mock_resolve:
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.get_client_type",
                            new_callable=AsyncMock,
                            return_value="Member",
                        ):
                            with patch(
                                "app.routers.ray.post_notification_ephemeral",
                                new_callable=AsyncMock,
                            ) as mock_post:
                                auth = RayEventAuth()
                                await auth.initialize(event, "valid-token")

                                await ray_events(event, auth)

        assert mock_resolve.await_args.args[0] == organization_uuid
        assert mock_resolve.await_args.kwargs["team_id"] == team_id
        assert mock_resolve.await_args.kwargs["slack_user_id"] == user_id
        mock_post.assert_awaited_once()
        delivered_user = mock_post.await_args.args[3]
        assert delivered_user.user_id == user_id
        assert delivered_user.user_id != organization_uuid

    @pytest.mark.asyncio
    async def test_ray_events_document_mt_quote_ibm_insufficient_balance_skips_token_prompt(
        self, mock_slack_user, user_id, team_id
    ):
        """IBM Grid quote shortfall alerts internally; SlackUser may lack enterprise_id."""
        assert mock_slack_user.enterprise_id is None
        organization_uuid = str(uuid4())
        event = RayEvent(
            event="verify:slack:document:quote",
            data={
                "quote_id": str(uuid4()),
                "client_id": organization_uuid,
                "channel_id": "C123",
                "error": True,
                "error_type": "insufficient_balance",
                "error_data": {"balance": 0, "required": 10},
                "team_id": team_id,
                "slack_user_id": user_id,
                "enterprise_id": "E27SFGS2W",
            },
        )
        mock_client = AsyncMock()

        with (
            patch("app.dependencies.validate_queue_proxy_secret", return_value=True),
            patch(
                "app.dependencies.resolve_slack_delivery_user",
                new_callable=AsyncMock,
                return_value=mock_slack_user,
            ),
            patch("app.dependencies.get_demo_link", return_value=[]),
            patch("app.routers.ray.AsyncWebClient", return_value=mock_client),
            patch(
                "app.routers.ray.get_client_type",
                new_callable=AsyncMock,
                return_value="Member",
            ),
            patch(
                "app.ray.utils.is_ibm_customer_enterprise",
                return_value=True,
            ),
            patch("app.auth.connector.notify_exception") as mock_notify,
            patch(
                "app.routers.ray.post_notification_ephemeral",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            auth = RayEventAuth()
            await auth.initialize(event, "valid-token")
            await ray_events(event, auth)

        mock_post.assert_not_awaited()
        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_document_translated_ibm_insufficient_balance_skips_token_prompt(
        self, mock_slack_user, user_id, team_id
    ):
        """IBM Grid document MT shortfall must not DM token copy."""
        assert mock_slack_user.enterprise_id is None
        event = RayEvent(
            event="verify:slack:document:translated",
            data={
                "error": True,
                "client_id": str(uuid4()),
                "channel_id": "C123",
                "error_type": "insufficient_balance",
                "error_data": {"balance": 0, "required": 100},
                "submission_id": 123,
                "team_id": team_id,
                "slack_user_id": user_id,
                "enterprise_id": "E27SFGS2W",
            },
        )
        mock_client = AsyncMock()

        with (
            patch("app.dependencies.validate_queue_proxy_secret", return_value=True),
            patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ),
            patch("app.dependencies.get_demo_link", return_value=[]),
            patch("app.routers.ray.AsyncWebClient", return_value=mock_client),
            patch("app.routers.ray.get_client_type", return_value="Admin"),
            patch(
                "app.ray.utils.is_ibm_customer_enterprise",
                return_value=True,
            ),
            patch("app.auth.connector.notify_exception") as mock_notify,
            patch(
                "app.routers.ray.post_notification_ephemeral",
                new_callable=AsyncMock,
            ) as mock_post,
            patch("app.routers.ray.updated_submission_status"),
        ):
            auth = RayEventAuth()
            await auth.initialize(event, "valid-token")
            await ray_events(event, auth)

        mock_post.assert_not_awaited()
        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_document_mt_quote_error_without_slack_user_is_logged(
        self, user_id
    ):
        """An unresolvable poster must be logged, not raised as a 500."""
        event = RayEvent(
            event="verify:slack:document:quote",
            data={
                "quote_id": str(uuid4()),
                "client_id": str(uuid4()),
                "channel_id": "C123",
                "error": True,
                "error_type": "insufficient_balance",
                "error_data": {"balance": 0, "required": 10},
            },
        )
        mock_client = AsyncMock()

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                new_callable=AsyncMock,
                return_value=None,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.post_notification_ephemeral",
                            new_callable=AsyncMock,
                        ) as mock_post:
                            auth = RayEventAuth()
                            await auth.initialize(event, "valid-token")

                            await ray_events(event, auth)

        mock_post.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error_type,error_data",
        [
            ("conversion_error", {"message": "extract failed"}),
            ("conversion_error", {}),
            ("file_complexity_error", {}),
            ("invalid_pdf", {}),
        ],
    )
    async def test_ray_events_document_translated_error_missing_keys_does_not_raise(
        self, mock_slack_user, user_id, team_id, error_type, error_data
    ):
        """Regression: error_data without the legacy 'ext'/'message' keys must
        not raise ``KeyError`` (RAY-79527).

        Producers (e.g. ``int-slack-verify-consumer``'s
        ``handle_extract_error``) historically published payloads that omit
        ``ext``/``file_expected``. The handler must degrade gracefully and
        still post an ephemeral notification.
        """
        payload = {
            "error": True,
            "client_id": str(uuid4()),
            "channel_id": "C123",
            "error_type": error_type,
            "error_data": error_data,
            "submission_id": 123,
        }
        event = RayEvent(
            event="verify:slack:document:translated",
            data={"client_id": mock_slack_user.ray_client_id, **payload},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.post_notification_ephemeral",
                            new_callable=AsyncMock,
                        ) as mock_post:
                            with patch("app.routers.ray.updated_submission_status"):
                                auth = RayEventAuth()
                                await auth.initialize(event, "valid-token")

                                await ray_events(event, auth)

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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.enqueue_mt_success_upload",
                            new_callable=AsyncMock,
                        ) as mock_enqueue:
                            auth = RayEventAuth()
                            await auth.initialize(event, "valid-token")

                            await ray_events(event, auth)

                            # MT success upload was enqueued onto SAQ (RAY-79638)
                            mock_enqueue.assert_called_once()

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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.claim_ray_event_notification",
                            new_callable=AsyncMock,
                            return_value=True,
                        ):
                            with patch(
                                "app.routers.ray.post_notification",
                                new_callable=AsyncMock,
                            ) as mock_post:
                                auth = RayEventAuth()
                                await auth.initialize(event, "valid-token")

                                await ray_events(event, auth)

                                # Verify error notification was sent
                                mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_evaluate_complete_ibm_insufficient_balance_skips_token_prompt(
        self, mock_slack_user, user_id, team_id
    ):
        """IBM Grid evaluate shortfall must not post token copy."""
        assert mock_slack_user.enterprise_id is None
        event = RayEvent(
            event="verify:slack:evaluate:complete",
            data={
                "error": True,
                "error_type": "insufficient_balance",
                "error_data": {"balance": 0, "required": 50},
                "client_id": mock_slack_user.ray_client_id,
                "channel_id": "C123",
                "job_uuid": str(uuid4()),
                "enterprise_id": "E27SFGS2W",
            },
        )
        mock_client = AsyncMock()

        with (
            patch("app.dependencies.validate_queue_proxy_secret", return_value=True),
            patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ),
            patch("app.dependencies.get_demo_link", return_value=[]),
            patch("app.routers.ray.AsyncWebClient", return_value=mock_client),
            patch("app.routers.ray.get_client_type", new_callable=AsyncMock),
            patch(
                "app.routers.ray.claim_ray_event_notification",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.ray.utils.is_ibm_customer_enterprise",
                return_value=True,
            ),
            patch("app.auth.connector.notify_exception") as mock_notify,
            patch(
                "app.routers.ray.post_notification",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            auth = RayEventAuth()
            await auth.initialize(event, "valid-token")
            await ray_events(event, auth)

        mock_post.assert_not_awaited()
        mock_notify.assert_called_once()

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

        with (
            patch("app.dependencies.validate_queue_proxy_secret", return_value=True),
            patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ),
            patch("app.dependencies.get_demo_link", return_value=[]),
            patch("app.routers.ray.AsyncWebClient", return_value=mock_client),
            patch("app.routers.ray.get_evaluation_job", return_value=mock_job),
            patch("app.routers.ray.get_language_name_by_uuid", return_value=[]),
            patch(
                "app.routers.ray.claim_ray_event_notification",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.routers.ray.job_is_human_translation_quote",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "app.routers.ray.post_notification",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            auth = RayEventAuth()
            await auth.initialize(event, "valid-token")

            await ray_events(event, auth)

            # Verify success notification was sent
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_evaluate_complete_staged_ht_keeps_ht_quote(
        self, mock_slack_user, user_id, team_id
    ):
        """Staged HT jobs have no HUMAN_EVALUATION workflow, so the quote session decides.

        Rendering the QE Evaluation Result here replaces the human translation
        quote with scores and a "Send for Human Verification" button.
        """
        from app.slack.templates.messages import HumanJobQuoteMessage

        job_uuid = str(uuid4())
        lang_uuid = str(uuid4())
        mock_job = {
            "data": {
                "uuid": job_uuid,
                "workflow_uuid": None,
                "extra_info": {},
                "human_job_in_progress": False,
                "source_files": [
                    {
                        "file_uuid": str(uuid4()),
                        "filename": "test.txt",
                        "target_files": [
                            {
                                "language_uuid": lang_uuid,
                                "human_job_status": None,
                                "target_file_uuid": str(uuid4()),
                            }
                        ],
                    }
                ],
                "target_languages": [{"uuid": lang_uuid, "name": "French"}],
            }
        }
        event = RayEvent(
            event="verify:slack:evaluate:complete",
            data={
                "client_id": mock_slack_user.ray_client_id,
                "error": False,
                "job_uuid": job_uuid,
            },
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }

        with (
            patch("app.dependencies.validate_queue_proxy_secret", return_value=True),
            patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ),
            patch("app.dependencies.get_demo_link", return_value=[]),
            patch("app.routers.ray.AsyncWebClient", return_value=mock_client),
            patch("app.routers.ray.get_evaluation_job", return_value=mock_job),
            patch(
                "app.routers.ray.claim_ray_event_notification",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.slack.evaluation_quotes.get_evaluate_quote_session",
                new_callable=AsyncMock,
                return_value={"quote_snapshot": {"auto_submit_human_job": True}},
            ),
            patch(
                "app.routers.ray.handle_combined_qe_complete",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "app.routers.ray.get_ray_client",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch(
                "app.routers.ray.get_job_pricing",
                new_callable=AsyncMock,
                return_value={"data": []},
            ),
            patch(
                "app.routers.ray.post_notification",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            auth = RayEventAuth()
            await auth.initialize(event, "valid-token")

            await ray_events(event, auth)

        mock_post.assert_called_once()
        assert isinstance(mock_post.call_args.args[3], HumanJobQuoteMessage)

    @pytest.mark.asyncio
    async def test_ray_events_evaluate_complete_duplicate_is_skipped(
        self, mock_slack_user, user_id, team_id
    ):
        """Duplicate evaluate-complete events do not post duplicate Slack messages."""
        event = RayEvent(
            event="verify:slack:evaluate:complete",
            data={
                "client_id": mock_slack_user.ray_client_id,
                "error": False,
                "job_uuid": str(uuid4()),
                "tokens": 50,
            },
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {"id": user_id, "locale": "en-US", "tz": "America/New_York"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.claim_ray_event_notification",
                            new_callable=AsyncMock,
                            return_value=False,
                        ):
                            with patch(
                                "app.routers.ray.post_notification",
                                new_callable=AsyncMock,
                            ) as mock_post:
                                auth = RayEventAuth()
                                await auth.initialize(event, "valid-token")

                                response = await ray_events(event, auth)

                                assert response == {
                                    "message": "Duplicate evaluate-complete event skipped"
                                }
                                mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_claim_ray_event_notification_claims_once(self):
        """The evaluate-complete idempotency guard uses an atomic Redis claim."""
        event = RayEvent(
            event="verify:slack:evaluate:complete",
            data={"client_id": "client-1", "job_uuid": "job-1"},
        )

        with patch(
            "app.ray.events.evaluate_quote_events.redis_conn.set",
            new_callable=AsyncMock,
        ) as mock_set:
            mock_set.return_value = True

            claimed = await claim_ray_event_notification(event)

            assert claimed is True
            mock_set.assert_awaited_once_with(
                "ray_event:verify:slack:evaluate:complete:client-1:job-1",
                "1",
                ex=RAY_EVENT_DEDUPE_TTL_SECONDS,
                nx=True,
            )

    @pytest.mark.asyncio
    async def test_claim_ray_event_notification_rejects_duplicate(self):
        """Redis NX misses are treated as duplicate events."""
        event = RayEvent(
            event="verify:slack:evaluate:complete",
            data={"client_id": "client-1", "job_uuid": "job-1"},
        )

        with patch(
            "app.ray.events.evaluate_quote_events.redis_conn.set",
            new_callable=AsyncMock,
        ) as mock_set:
            mock_set.return_value = None

            claimed = await claim_ray_event_notification(event)

            assert claimed is False

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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.get_language_name_by_uuid",
                            return_value="French",
                        ):
                            with patch(
                                "app.routers.ray.post_notification",
                                new_callable=AsyncMock,
                                return_value=mock_response,
                            ) as mock_post:
                                with patch(
                                    "app.routers.ray.enqueue_verify_complete_upload",
                                    new_callable=AsyncMock,
                                ) as mock_enqueue:
                                    auth = RayEventAuth()
                                    await auth.initialize(event, "valid-token")

                                    await ray_events(event, auth)

                                    # Verify notification was sent and durable upload was enqueued (RAY-79638)
                                    mock_post.assert_called_once()
                                    mock_enqueue.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_direct_mt_result(self, mock_slack_user, user_id, team_id):
        """Test direct MT result event."""
        extra_data = {
            "client_id": str(uuid4()),
            "service_language_mapping": {"google": {"fr": ""}},
            "source_language": "en",
            "organization_uuid": str(uuid4()),
            "team_id": team_id,
            "group_id": str(uuid4()),
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
            "user": {
                "id": user_id,
                "locale": "en-US",
                "tz": "America/New_York",
                "profile": {"email": "test@example.com"},
            }
        }
        mock_client.conversations_info.return_value = {
            "channel": {"name": "test-channel"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.post_notification", new_callable=AsyncMock
                        ) as mock_post:
                            with patch(
                                "app.routers.ray.enqueue_inline_mt_billing",
                                new_callable=AsyncMock,
                            ) as mock_bill:
                                auth = RayEventAuth()
                                await auth.initialize(event, "valid-token")

                                await ray_events(event, auth)

                                # Verify notification was sent
                                mock_post.assert_called_once()
                                # Billing is now deferred to a durable SAQ job so
                                # a gateway timeout cannot 422 the callback after
                                # the Slack message was posted (RAY-80258).
                                mock_bill.assert_called_once()
                                billing = mock_bill.call_args.kwargs["billing"]
                                assert (
                                    billing["client_id"]
                                    == mock_slack_user.ray_client_id
                                )
                                assert billing["usage_type"] == (
                                    "direct_machine_translation"
                                )
                                # No message_ts -> a stable fallback key is still
                                # derived so the SAQ charge is idempotent.
                                assert billing["idempotency_key"]
                                assert (
                                    mock_bill.call_args.kwargs["idempotency_key"]
                                    == billing["idempotency_key"]
                                )

    @pytest.mark.asyncio
    async def test_ray_events_channel_translation_result(
        self, mock_slack_user, user_id, team_id
    ):
        """Test channel translation result event."""
        extra_data = {
            "client_id": str(uuid4()),
            "service_language_mapping": {"google": {"af": "", "fr": "", "la": ""}},
            "source_language": "en",
            "organization_uuid": str(uuid4()),
            "team_id": team_id,
            "group_id": str(uuid4()),
            "channel_id": "C123",
            "text_length": 100,
            "usage_type": "channel_translation",
            "source_text": "Hello world",
            "target_language_order": ["la", "af", "fr"],
            "display_format": "thread",
            "message_ts": "123456.789",
        }
        event_data = {
            "extra_data": extra_data,
            "translations": {
                "af": ["Hallo"],
                "fr": ["Bonjour"],
                "la": ["Salve"],
            },
        }
        event = RayEvent(
            event="slack:direct:mt:result",
            data={"client_id": mock_slack_user.ray_client_id, **event_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {
                "id": user_id,
                "locale": "en-US",
                "tz": "America/New_York",
                "profile": {"email": "test@example.com"},
            }
        }
        mock_client.conversations_info.return_value = {
            "channel": {"name": "test-channel"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.post_channel_translation_notification",
                            new_callable=AsyncMock,
                        ) as mock_post:
                            with patch(
                                "app.routers.ray.enqueue_inline_mt_billing",
                                new_callable=AsyncMock,
                            ) as mock_bill:
                                auth = RayEventAuth()
                                await auth.initialize(event, "valid-token")

                                await ray_events(event, auth)

                                # Verify channel translation notification was sent
                                mock_post.assert_called_once()
                                message = mock_post.call_args.args[3]
                                assert list(message.translations.keys()) == [
                                    "la",
                                    "af",
                                    "fr",
                                ]
                                # Billing is deferred to a durable SAQ job that
                                # charges the gateway and writes the
                                # credit_transaction_usage row (RAY-80000 / 80258).
                                mock_bill.assert_called_once()

    @pytest.mark.asyncio
    async def test_ray_events_channel_translation_skipped_when_source_deleted(
        self, mock_slack_user, user_id, team_id
    ):
        """An in-flight channel translation must not post if the source message
        was deleted while the request was outstanding (RAY-80512)."""
        extra_data = {
            "client_id": str(uuid4()),
            "service_language_mapping": {"google": {"fr": ""}},
            "source_language": "en",
            "organization_uuid": str(uuid4()),
            "team_id": team_id,
            "group_id": str(uuid4()),
            "channel_id": "C123",
            "text_length": 100,
            "usage_type": "channel_translation",
            "source_text": "Hello world",
            "target_language_order": ["fr"],
            "display_format": "thread",
            "message_ts": "123456.789",
        }
        event = RayEvent(
            event="slack:direct:mt:result",
            data={
                "client_id": mock_slack_user.ray_client_id,
                "extra_data": extra_data,
                "translations": {"fr": ["Bonjour"]},
            },
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {
                "id": user_id,
                "locale": "en-US",
                "tz": "America/New_York",
                "profile": {"email": "test@example.com"},
            }
        }
        mock_client.conversations_info.return_value = {
            "channel": {"name": "test-channel"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.post_channel_translation_notification",
                            new_callable=AsyncMock,
                        ) as mock_post:
                            with patch(
                                "app.slack.bot_translation.is_channel_source_deleted",
                                new_callable=AsyncMock,
                                return_value=True,
                            ):
                                with patch(
                                    "app.routers.ray.enqueue_inline_mt_billing",
                                    new_callable=AsyncMock,
                                ):
                                    auth = RayEventAuth()
                                    await auth.initialize(event, "valid-token")

                                    result = await ray_events(event, auth)

                                    mock_post.assert_not_called()
                                    assert (
                                        result["message"]
                                        == "Deleted source channel translation skipped"
                                    )

    @pytest.mark.asyncio
    async def test_ray_events_channel_translation_result_bot_reporting(self, team_id):
        """Bot channel translation reports bot name and is_bot metadata."""
        bot_slack_user = SlackUser(
            user_id="U_BOT_USER",
            team_id=team_id,
            enterprise_id=None,
            channel_id="C123",
            bot_token="xoxb-test-token",
            ray_client_id=str(uuid4()),
            ray_username="bot-org",
            ray_user_group_id="billing-group",
            is_subscribed=True,
        )
        extra_data = {
            "client_id": bot_slack_user.ray_client_id,
            "service_language_mapping": {"google": {"fr": ""}},
            "source_language": "en",
            "organization_uuid": str(uuid4()),
            "team_id": team_id,
            "group_id": "billing-group",
            "channel_id": "C123",
            "text_length": 100,
            "usage_type": "channel_translation",
            "source_text": "Hello world",
            "target_language_order": ["fr"],
            "display_format": "thread",
            "message_ts": "123456.789",
            "slack_user_id": "U_BOT_USER",
            "slack_user_name": "Deploy Bot",
            "is_bot": True,
        }
        event = RayEvent(
            event="slack:direct:mt:result",
            data={
                "client_id": bot_slack_user.ray_client_id,
                "extra_data": extra_data,
                "translations": {"fr": ["Bonjour"]},
            },
        )

        mock_client = AsyncMock()
        mock_client.users_info.side_effect = RuntimeError("bot profile unavailable")
        mock_client.conversations_info.return_value = {
            "channel": {"name": "test-channel"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=bot_slack_user,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.post_channel_translation_notification",
                            new_callable=AsyncMock,
                        ):
                            with patch(
                                "app.routers.ray.enqueue_inline_mt_billing",
                                new_callable=AsyncMock,
                            ) as mock_bill:
                                auth = RayEventAuth()
                                await auth.initialize(event, "valid-token")

                                await ray_events(event, auth)

                                # Billing is deferred to the durable SAQ job
                                # (RAY-80258); bot identity must still be carried
                                # on the billing payload (RAY-80133).
                                mock_bill.assert_called_once()
                                billing = mock_bill.call_args.kwargs["billing"]
                                assert billing["client_name"] == "Deploy Bot"
                                assert billing["email"] is None
                                assert billing["is_bot"] is True

    @pytest.mark.asyncio
    async def test_ray_events_channel_translation_result_null_group_id(
        self, user_id, team_id
    ):
        """Channel translation must not fail when the poster's default group
        (`obj_m_member.groupid`) is NULL: the callback should still post, charge,
        and log usage using the submission's group_id (RAY-80199)."""
        # Logged-in poster whose member row has no default group.
        slack_user_no_group = SlackUser(
            user_id=user_id,
            team_id=team_id,
            enterprise_id=None,
            channel_id=user_id,
            bot_token="xoxb-test-token",
            ray_client_id=str(uuid4()),
            ray_username="test.user",
            ray_user_group_id=None,
            is_subscribed=True,
        )
        submission_group_id = str(uuid4())
        extra_data = {
            "client_id": str(uuid4()),
            "service_language_mapping": {"google": {"ko": ""}},
            "source_language": "en",
            "organization_uuid": str(uuid4()),
            "team_id": team_id,
            "group_id": submission_group_id,
            "channel_id": "C123",
            "text_length": 65,
            "usage_type": "channel_translation",
            "source_text": "can u debug?",
            "target_language_order": ["ko"],
            "display_format": "thread",
            "message_ts": "123456.789",
        }
        event_data = {
            "extra_data": extra_data,
            "translations": {"ko": ["디버깅"]},
        }
        event = RayEvent(
            event="slack:direct:mt:result",
            data={"client_id": slack_user_no_group.ray_client_id, **event_data},
        )

        mock_client = AsyncMock()
        mock_client.users_info.return_value = {
            "user": {
                "id": user_id,
                "locale": "en-US",
                "tz": "America/New_York",
                "profile": {"email": "test@example.com"},
            }
        }
        mock_client.conversations_info.return_value = {
            "channel": {"name": "test-channel"}
        }

        with patch("app.dependencies.validate_queue_proxy_secret", return_value=True):
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=slack_user_no_group,
            ):
                with patch("app.dependencies.get_demo_link", return_value=[]):
                    with patch(
                        "app.routers.ray.AsyncWebClient", return_value=mock_client
                    ):
                        with patch(
                            "app.routers.ray.post_channel_translation_notification",
                            new_callable=AsyncMock,
                        ) as mock_post:
                            with patch(
                                "app.routers.ray.enqueue_inline_mt_billing",
                                new_callable=AsyncMock,
                            ) as mock_bill:
                                auth = RayEventAuth()
                                await auth.initialize(event, "valid-token")

                                # Must not raise (previously a bare assert on
                                # ray_user_group_id produced a 422).
                                await ray_events(event, auth)

                                mock_post.assert_called_once()
                                mock_bill.assert_called_once()
                                # Usage log falls back to the submission's
                                # group_id when the default group is NULL; the
                                # value is carried in the deferred billing payload.
                                usage_log = mock_bill.call_args.kwargs["usage_log"]
                                assert usage_log["group_uuid"] == submission_group_id

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
            with patch(
                "app.dependencies.resolve_slack_delivery_user",
                return_value=mock_slack_user,
            ):
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
