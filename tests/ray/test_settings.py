from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models import SlackGroupSettingsTranslationLangs
from app.ray import settings as ray_settings


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.executed_query = None

    async def execute(self, query):
        self.executed_query = query
        return _FakeResult(self.rows)


class _FakeAsyncSession:
    last_session = None

    def __init__(self, *_args, **_kwargs):
        rows = [
            SimpleNamespace(target_lang="la", display_format="thread"),
            SimpleNamespace(target_lang="af", display_format="thread"),
            SimpleNamespace(target_lang="la", display_format="thread"),
            SimpleNamespace(target_lang="fr", display_format="thread"),
        ]
        self.session = _FakeSession(rows)
        _FakeAsyncSession.last_session = self.session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_get_auto_translate_settings_and_langs_preserves_insert_order(
    monkeypatch,
):
    monkeypatch.setattr(
        ray_settings, "get_all_settings_for_channel", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(ray_settings, "AsyncSession", _FakeAsyncSession)

    settings = await ray_settings.get_auto_translate_settings_and_langs(
        context=SimpleNamespace(team_id="T123"),
        channel_id="C123",
    )

    assert settings == [
        {"target_lang": "la", "display_format": "thread"},
        {"target_lang": "af", "display_format": "thread"},
        {"target_lang": "fr", "display_format": "thread"},
    ]
    assert _FakeAsyncSession.last_session is not None
    query = _FakeAsyncSession.last_session.executed_query
    assert query is not None
    order_by_clauses = tuple(query._order_by_clauses)
    assert order_by_clauses
    assert order_by_clauses[0].compare(
        SlackGroupSettingsTranslationLangs.id.__clause_element__()
    )
