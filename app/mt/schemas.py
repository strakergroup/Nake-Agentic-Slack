from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TranslationRequest(BaseModel):
    text: str
    target_languages: List[str]
    source_language: str | None = None
    app_name: str
    usage_type: str = "direct_machine_translation"
    email: str | None = (
        None  # used when user is not logged in or does not have an lc account
    )
    group_uuid: str | None = None
    channel_name: str | None = None


class TranslationResponse(BaseModel):
    source_language: str
    translations: dict[str, str]


class MultiLanguageTranslationResponse(BaseModel):
    """Response model for multi-language translation requests."""

    app_id: str = Field(..., description="Application ID")
    task_id: str = Field(..., description="Task ID")
    translations: Dict[str, List[str]] = Field(
        ...,
        description="Dictionary mapping language codes to lists of translated strings",
    )
    extra_data: Dict[str, Any] = Field(
        default_factory=dict, description="Additional data"
    )
    error: str = Field(default="", description="Error message if any")
    status: bool = Field(..., description="Success status")
    cache_key: Optional[str] = Field(None, description="Cache key for the translation")


class ErrorResponse(BaseModel):
    """Response model for error cases."""

    app_id: str = Field(..., description="Application ID")
    task_id: str = Field(..., description="Task ID")
    extra_data: Dict[str, Any] = Field(
        default_factory=dict, description="Additional data"
    )
    error: str = Field(..., description="Error message")
    status: bool = Field(
        default=False, description="Success status (always False for errors)"
    )
