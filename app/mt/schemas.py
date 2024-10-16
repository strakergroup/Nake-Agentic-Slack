from typing import List

from pydantic import BaseModel


class TranslationRequest(BaseModel):
    text: str
    target_languages: List[str]
    source_language: str | None = None
    app_name: str
    usage_type: str = "direct_machine_translation"
    email: str | None = (
        None  # used when user is not logged in or does not have an lc account
    )


class TranslationResponse(BaseModel):
    source_language: str
    translations: dict[str, str]
