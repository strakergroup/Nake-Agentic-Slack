import re
import pytest
from redis.asyncio import Redis

from app.slack.select_options import (
    _get_languages_cached,
    get_language_options,
    map_file_options,
    get_languages_sync,
    initialize_languages_cache,
)


@pytest.mark.asyncio
async def test_get_languages_cached(redis: Redis):
    key = "slack-ray-translator:languages:v1"
    await redis.delete(key)
    languages = await _get_languages_cached()
    assert isinstance(languages, list)
    assert len(languages)
    assert isinstance(languages[0], dict)
    assert "code" in languages[0]
    assert "name" in languages[0]
    # Test that the result is cached in Redis.
    ttl = await redis.ttl(key)
    assert ttl > 0, f"Expected TTL > 0, got {ttl}"


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
    await initialize_languages_cache()
    languages = get_languages_sync()
    assert isinstance(languages, list)
    assert len(languages) > 0
    assert isinstance(languages[0], dict)
    assert "code" in languages[0]
    assert "name" in languages[0]


@pytest.mark.asyncio
async def test_get_language_options():
    options = await get_language_options()
    assert isinstance(options, list)
    assert len(options)
    assert isinstance(options[0], dict)
    assert isinstance(options[0].get("text"), dict)
    assert options[0]["text"]["type"] == "plain_text"
    assert isinstance(options[0]["text"]["text"], str)
    assert isinstance(options[0]["value"], str)


@pytest.mark.asyncio
async def test_get_language_options_filter():
    options = await get_language_options("English")
    pattern = re.compile(r"english", re.IGNORECASE)
    for opt in options:
        assert pattern.search(opt["text"]["text"])
        assert pattern.search(opt["value"])

    options = await get_language_options("ja")
    pattern = re.compile(r"ja", re.IGNORECASE)
    for opt in options:
        assert pattern.search(opt["text"]["text"])
        assert pattern.search(opt["value"])


def test_map_file_options(message_file):
    options, initial_options = map_file_options([message_file])
    assert isinstance(options, list)
    assert len(options) == 1
    assert options == initial_options
    assert options[0] == {
        "text": {"type": "plain_text", "text": message_file["title"], "emoji": False},
        "value": message_file["id"],
    }
