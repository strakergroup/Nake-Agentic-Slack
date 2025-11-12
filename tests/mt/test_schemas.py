"""Tests for app/mt/schemas.py - MT request/response models."""

import pytest
from pydantic import ValidationError

from app.mt.schemas import (
    ErrorResponse,
    MultiLanguageTranslationResponse,
    TranslationRequest,
    TranslationResponse,
)


class TestTranslationRequest:
    """Tests for TranslationRequest model."""

    def test_translation_request_minimal(self):
        """Test TranslationRequest with minimal required fields."""
        request = TranslationRequest(
            text="Hello world",
            service_language_mapping={"service1": ["fr", "es"]},
            app_name="slack",
        )

        assert request.text == "Hello world"
        assert request.service_language_mapping == {"service1": ["fr", "es"]}
        assert request.app_name == "slack"
        assert request.usage_type == "direct_machine_translation"  # Default
        assert request.source_language is None
        assert request.email is None
        assert request.group_uuid is None
        assert request.channel_name is None

    def test_translation_request_full(self):
        """Test TranslationRequest with all fields."""
        request = TranslationRequest(
            text="Hello world",
            service_language_mapping={"service1": ["fr", "es"]},
            app_name="slack",
            source_language="en",
            usage_type="channel_translation",
            email="test@example.com",
            group_uuid="group-123",
            channel_name="general",
        )

        assert request.text == "Hello world"
        assert request.service_language_mapping == {"service1": ["fr", "es"]}
        assert request.app_name == "slack"
        assert request.source_language == "en"
        assert request.usage_type == "channel_translation"
        assert request.email == "test@example.com"
        assert request.group_uuid == "group-123"
        assert request.channel_name == "general"

    def test_translation_request_missing_required_fields(self):
        """Test TranslationRequest validation with missing required fields."""
        with pytest.raises(ValidationError):
            TranslationRequest(
                text="Hello world",
                # Missing service_language_mapping
                app_name="slack",
            )


class TestTranslationResponse:
    """Tests for TranslationResponse model."""

    def test_translation_response(self):
        """Test TranslationResponse model."""
        response = TranslationResponse(
            source_language="en",
            translations={"fr": "Bonjour", "es": "Hola"},
        )

        assert response.source_language == "en"
        assert response.translations == {"fr": "Bonjour", "es": "Hola"}


class TestMultiLanguageTranslationResponse:
    """Tests for MultiLanguageTranslationResponse model."""

    def test_multi_language_translation_response_minimal(self):
        """Test MultiLanguageTranslationResponse with minimal fields."""
        response = MultiLanguageTranslationResponse(
            app_id="app-123",
            task_id="task-456",
            translations={"fr": ["Bonjour"], "es": ["Hola"]},
            status=True,
        )

        assert response.app_id == "app-123"
        assert response.task_id == "task-456"
        assert response.translations == {"fr": ["Bonjour"], "es": ["Hola"]}
        assert response.status is True
        assert response.error == ""  # Default
        assert response.extra_data == {}  # Default
        assert response.cache_key is None

    def test_multi_language_translation_response_full(self):
        """Test MultiLanguageTranslationResponse with all fields."""
        response = MultiLanguageTranslationResponse(
            app_id="app-123",
            task_id="task-456",
            translations={"fr": ["Bonjour", "le monde"], "es": ["Hola", "mundo"]},
            status=True,
            error="",
            extra_data={"key": "value"},
            cache_key="cache-789",
        )

        assert response.app_id == "app-123"
        assert response.task_id == "task-456"
        assert response.translations == {
            "fr": ["Bonjour", "le monde"],
            "es": ["Hola", "mundo"],
        }
        assert response.status is True
        assert response.error == ""
        assert response.extra_data == {"key": "value"}
        assert response.cache_key == "cache-789"


class TestErrorResponse:
    """Tests for ErrorResponse model."""

    def test_error_response_minimal(self):
        """Test ErrorResponse with minimal fields."""
        response = ErrorResponse(
            app_id="app-123",
            task_id="task-456",
            error="Something went wrong",
        )

        assert response.app_id == "app-123"
        assert response.task_id == "task-456"
        assert response.error == "Something went wrong"
        assert response.status is False  # Default
        assert response.extra_data == {}  # Default

    def test_error_response_full(self):
        """Test ErrorResponse with all fields."""
        response = ErrorResponse(
            app_id="app-123",
            task_id="task-456",
            error="Something went wrong",
            status=False,
            extra_data={"details": "More info"},
        )

        assert response.app_id == "app-123"
        assert response.task_id == "task-456"
        assert response.error == "Something went wrong"
        assert response.status is False
        assert response.extra_data == {"details": "More info"}
