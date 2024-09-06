from typing import List

from pydantic import BaseModel


class TranslationRequest(BaseModel):
    text: str
    target_languages: List[str]
    source_language: str | None = None


class TranslationResponse(BaseModel):
    source_language: str
    translations: dict[str, str]
