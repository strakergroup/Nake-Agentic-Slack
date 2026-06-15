from unittest.mock import AsyncMock, patch

import pytest

from app.mt.service import (
    evaluate_get_glossary_resource,
    glossary_language_candidates,
    is_no_op_translation_pair,
    resolve_language,
)


class TestGlossaryLanguageCandidates:
    def test_non_english_returns_single_candidate(self):
        assert glossary_language_candidates("fr-ca") == ["fr-ca"]

    def test_english_returns_variant_candidates(self):
        assert glossary_language_candidates("en") == ["en", "en-us", "en-gb"]

    def test_regional_english_keeps_exact_first(self):
        assert glossary_language_candidates("EN-US") == ["en-us", "en", "en-gb"]


class TestIsNoOpTranslationPair:
    @pytest.mark.parametrize(
        "source,target",
        [
            ("en", "en"),
            ("EN", "en"),
            ("zh-CN", "zh-cn"),
            (" fr ", "fr"),
            ("zh", "zh-CN"),
            ("zh-CN", "zh"),
            ("zh-TW", "zh"),
            ("fr", "fr-ca"),
            ("fr-ca", "fr"),
            ("pt", "pt-BR"),
            ("pt-BR", "pt"),
            ("es", "es-419"),
            ("es-419", "es"),
            ("en-US", "en"),
        ],
    )
    def test_returns_true_for_no_op_pairs(self, source, target):
        assert is_no_op_translation_pair(source, target) is True

    @pytest.mark.parametrize(
        "source,target",
        [
            ("zh-CN", "zh-TW"),
            ("zh-TW", "zh-CN"),
            ("pt-BR", "pt-PT"),
            ("en-US", "en-GB"),
            ("en", "fr"),
            ("zh-CN", "ja"),
            ("fr-ca", "en"),
        ],
    )
    def test_returns_false_for_distinct_pairs(self, source, target):
        assert is_no_op_translation_pair(source, target) is False

    @pytest.mark.parametrize(
        "source,target",
        [
            (None, "en"),
            ("en", None),
            ("", "en"),
            ("en", ""),
            ("   ", "en"),
        ],
    )
    def test_missing_or_blank_inputs_return_false(self, source, target):
        assert is_no_op_translation_pair(source, target) is False


@pytest.mark.asyncio
@patch("app.mt.service.evaluate_get_org_groups", new_callable=AsyncMock)
@patch("app.mt.service.fetch_one", new_callable=AsyncMock)
async def test_evaluate_get_glossary_resource_tries_english_variants(
    mock_fetch_one, mock_get_org_groups
):
    mock_get_org_groups.return_value = ["group-1"]

    async def _fetch_one_side_effect(query, _engine):
        params = {name: bind.value for name, bind in query._bindparams.items()}
        if params["sl"] == "en-us" and params["tl"] == "fr-ca":
            return {"terminology_id": "glossary-123"}
        return None

    mock_fetch_one.side_effect = _fetch_one_side_effect

    result = await evaluate_get_glossary_resource(
        org_uuid="org-1",
        client=None,
        sl="en",
        tl="fr-ca",
        engine="microsoft",
    )

    assert result == "glossary-123"
    attempted_pairs = [
        (call.args[0]._bindparams["sl"].value, call.args[0]._bindparams["tl"].value)
        for call in mock_fetch_one.await_args_list
    ]
    assert attempted_pairs[:2] == [("en", "fr-ca"), ("en-us", "fr-ca")]


@pytest.mark.asyncio
@patch("app.mt.service.evaluate_get_org_groups", new_callable=AsyncMock)
@patch("app.mt.service.fetch_one", new_callable=AsyncMock)
async def test_evaluate_get_glossary_resource_prefers_exact_match(
    mock_fetch_one, mock_get_org_groups
):
    mock_get_org_groups.return_value = ["group-1"]
    mock_fetch_one.return_value = {"terminology_id": "glossary-exact"}

    result = await evaluate_get_glossary_resource(
        org_uuid="org-1",
        client=None,
        sl="en-us",
        tl="fr-ca",
        engine="microsoft",
    )

    assert result == "glossary-exact"
    assert mock_fetch_one.await_count == 1


@pytest.mark.asyncio
async def test_resolve_language_keeps_es_419_for_google():
    result = await resolve_language(["es-419"], engine="google")
    assert result == ["es-419"]
