import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.asyncio import Redis

from app.slack.select_options import (
    _get_languages_cached,
    get_language_options,
    get_languages_sync,
    initialize_languages_cache,
    map_file_options,
)


@pytest.mark.asyncio
async def test_get_languages_cached(redis: Redis):
    key = "slack-ray-translator:languages:v1"
    await redis.delete(key)

    # Mock the get_languages API call
    # Create proper mock language objects with attributes
    mock_lang_en = MagicMock()
    mock_lang_en.code = "en"
    mock_lang_en.name = "English"
    mock_lang_en.uuid = "uuid-en"

    mock_lang_fr = MagicMock()
    mock_lang_fr.code = "fr"
    mock_lang_fr.name = "French"
    mock_lang_fr.uuid = "uuid-fr"

    mock_languages = [mock_lang_en, mock_lang_fr]
    mock_response = MagicMock(data=mock_languages)

    # Mock Redis get to return None (cache miss) so it calls the API
    with patch(
        "app.slack.select_options.redis_conn.get", new_callable=AsyncMock
    ) as mock_redis_get:
        mock_redis_get.return_value = None  # Cache miss
        with patch(
            "app.slack.select_options.get_languages", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = mock_response
            with patch(
                "app.slack.select_options.redis_conn.set", new_callable=AsyncMock
            ) as mock_redis_set:
                languages = await _get_languages_cached()
                assert isinstance(languages, list)
                assert len(languages) == 2
                assert isinstance(languages[0], dict)
                assert "code" in languages[0]
                assert "name" in languages[0]
                assert languages[0]["code"] == "en"
                assert languages[0]["name"] == "English"
                # Verify it was cached
                mock_redis_set.assert_called_once()


def test_get_languages_sync():
    """Test the synchronous getter for cached languages."""
    languages = get_languages_sync()
    assert isinstance(languages, list)
    # The cache might be empty if not initialized, but should still be a list
    if languages:
        assert isinstance(languages[0], dict)
        assert "code" in languages[0]
        assert "name" in languages[0]


@pytest.mark.asyncio
async def test_initialize_languages_cache():
    """Test initializing the languages cache."""
    # Mock the get_languages API call
    # Create proper mock language objects with attributes
    mock_lang_en = MagicMock()
    mock_lang_en.code = "en"
    mock_lang_en.name = "English"
    mock_lang_en.uuid = "uuid-en"

    mock_lang_fr = MagicMock()
    mock_lang_fr.code = "fr"
    mock_lang_fr.name = "French"
    mock_lang_fr.uuid = "uuid-fr"

    mock_languages = [mock_lang_en, mock_lang_fr]
    mock_response = MagicMock(data=mock_languages)

    with patch(
        "app.slack.select_options.get_languages", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = mock_response
        await initialize_languages_cache()
        languages = get_languages_sync()
        assert isinstance(languages, list)
        assert len(languages) > 0
        assert isinstance(languages[0], dict)
        assert "code" in languages[0]
        assert "name" in languages[0]


@pytest.mark.asyncio
async def test_get_language_options():
    # Mock the get_languages API call
    # Create proper mock language objects with attributes
    mock_lang_en = MagicMock()
    mock_lang_en.code = "en"
    mock_lang_en.name = "English"
    mock_lang_en.uuid = "uuid-en"

    mock_lang_fr = MagicMock()
    mock_lang_fr.code = "fr"
    mock_lang_fr.name = "French"
    mock_lang_fr.uuid = "uuid-fr"

    mock_languages = [mock_lang_en, mock_lang_fr]
    mock_response = MagicMock(data=mock_languages)

    with patch(
        "app.slack.select_options._get_languages_cached", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = [
            {"code": "en", "name": "English", "uuid": "uuid-en"},
            {"code": "fr", "name": "French", "uuid": "uuid-fr"},
        ]
        options = await get_language_options()
        assert isinstance(options, list)
        assert len(options) == 2
        assert isinstance(options[0], dict)
        assert isinstance(options[0].get("text"), dict)
        assert options[0]["text"]["type"] == "plain_text"
        assert isinstance(options[0]["text"]["text"], str)
        assert isinstance(options[0]["value"], str)


@pytest.mark.asyncio
async def test_get_language_options_uuid_uses_verify_languages():
    with patch(
        "app.slack.select_options.get_verify_languages", new_callable=AsyncMock
    ) as mock_get_verify_languages:
        mock_get_verify_languages.return_value = [
            {"code": "fr", "name": "French", "uuid": "uuid-fr"},
            {"code": "en", "name": "English", "uuid": "uuid-en"},
        ]

        options = await get_language_options(format="uuid")

        mock_get_verify_languages.assert_called_once()
        assert [option["value"] for option in options] == ["uuid-en", "uuid-fr"]
        assert [option["text"]["text"] for option in options] == ["English", "French"]


@pytest.mark.asyncio
async def test_get_source_language_options_uses_verify_source_languages():
    with patch(
        "app.slack.select_options.get_verify_source_languages", new_callable=AsyncMock
    ) as mock_get_verify_source_languages:
        mock_get_verify_source_languages.return_value = [
            {"code": "pt-BR", "name": "Portuguese", "uuid": "uuid-pt"},
            {"code": "en-US", "name": "English", "uuid": "uuid-en"},
        ]

        options = await get_language_options(format="uuid", source_only=True)

        mock_get_verify_source_languages.assert_called_once()
        assert [option["value"] for option in options] == ["uuid-en", "uuid-pt"]
        assert [option["text"]["text"] for option in options] == [
            "English",
            "Portuguese",
        ]


@pytest.mark.asyncio
async def test_get_language_options_filter():
    # Mock the get_languages API call
    # Create proper mock language objects with attributes
    mock_lang_en = MagicMock()
    mock_lang_en.code = "en"
    mock_lang_en.name = "English"
    mock_lang_en.uuid = "uuid-en"

    mock_lang_ja = MagicMock()
    mock_lang_ja.code = "ja"
    mock_lang_ja.name = "Japanese"
    mock_lang_ja.uuid = "uuid-ja"

    mock_languages = [mock_lang_en, mock_lang_ja]
    mock_response = MagicMock(data=mock_languages)

    with patch(
        "app.slack.select_options._get_languages_cached", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = [
            {"code": "en", "name": "English", "uuid": "uuid-en"},
            {"code": "ja", "name": "Japanese", "uuid": "uuid-ja"},
        ]
        options = await get_language_options("English")
        pattern = re.compile(r"english", re.IGNORECASE)
        for opt in options:
            # The text should match "English" (name)
            assert pattern.search(opt["text"]["text"])
            # The value is the code "en", not the name, so it won't match "english"
            # But we can verify it's a valid code
            assert opt["value"] == "en"

        options = await get_language_options("ja")
        pattern = re.compile(r"ja", re.IGNORECASE)
        for opt in options:
            # Both text and value should match "ja" (code)
            assert pattern.search(opt["text"]["text"]) or pattern.search(opt["value"])
            assert pattern.search(opt["value"])


def test_map_file_options(message_file):
    options, initial_options = map_file_options([message_file])
    assert isinstance(options, list)
    assert len(options) == 1
    assert options == initial_options
    assert options[0] == {
        "text": {"type": "plain_text", "text": message_file["title"], "emoji": False},
        "value": f"{message_file['id']}|{message_file['size']}",
    }
