from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_engines
from app.models import Language


def get_language_base_code(language_code: str | None) -> str:
    """Return the base language code used for family comparisons."""
    if not language_code:
        return ""
    return language_code.split("-")[0].split("_")[0].lower()


def is_same_language_family(source_code: str | None, target_code: str | None) -> bool:
    """Match Cloud Verify language-family validation semantics."""
    source_base = get_language_base_code(source_code)
    target_base = get_language_base_code(target_code)
    if not source_base or not target_base:
        return False
    return source_base == target_base


def get_same_family_target_codes(
    source_code: str | None, target_codes: Sequence[str]
) -> list[str]:
    """Return target option values in the same language family as ``source_code``.

    Used by Document MT modal submit to reject pairs such as ``es``→``es-419``
    or ``fr``→``fr-CA`` before a job is queued (RAY-80734).
    """
    if not source_code or not target_codes:
        return []
    conflicts: list[str] = []
    seen: set[str] = set()
    for raw in target_codes:
        code = str(raw or "").strip()
        if not code or code in seen:
            continue
        if not is_same_language_family(source_code, code):
            continue
        seen.add(code)
        conflicts.append(code)
    return conflicts


def get_conflicting_target_language_labels_from_rows(
    source_uuid: str, target_uuids: Sequence[str], languages: Sequence[Language]
) -> list[str]:
    """Return target labels that belong to the same family as the source."""
    language_by_uuid = {language.uuid: language for language in languages}
    source_language = language_by_uuid.get(source_uuid)
    if not source_language:
        return []

    conflicting_labels: list[str] = []
    seen_labels: set[str] = set()
    for target_uuid in target_uuids:
        target_language = language_by_uuid.get(target_uuid)
        if not target_language:
            continue
        if not is_same_language_family(
            source_language.google_code, target_language.google_code
        ):
            continue
        if target_language.label in seen_labels:
            continue
        seen_labels.add(target_language.label)
        conflicting_labels.append(target_language.label)

    return conflicting_labels


async def get_conflicting_target_language_labels(
    source_uuid: str, target_uuids: Sequence[str]
) -> list[str]:
    """Look up languages by UUID and return same-family target labels."""
    uuids = list(dict.fromkeys([source_uuid, *target_uuids]))
    if not source_uuid or not target_uuids:
        return []

    async with AsyncSession(async_engines["translators_readonly"]) as session:
        result = await session.execute(select(Language).where(Language.uuid.in_(uuids)))
        languages = list(result.scalars().all())

    return get_conflicting_target_language_labels_from_rows(
        source_uuid, target_uuids, languages
    )
