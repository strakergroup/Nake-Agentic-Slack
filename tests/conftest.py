import asyncio
from random import randrange
from typing import Any
from uuid import uuid4

import pytest
from slack_bolt.context.async_context import AsyncBoltContext

from app.auth.connector import RayClient
from app.redis import redis_conn
from app.slack.select_options import _cached_languages

# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------


def mock_user_id() -> str:
    return f"U{randrange(0, 10_000_000_000):010}"


def mock_team_id() -> str:
    return f"T{randrange(0, 10_000_000_000):010}"


def mock_enterprise_id() -> str:
    return f"E{randrange(0, 10_000_000_000):010}"


def mock_app_id() -> str:
    return f"A{randrange(0, 10_000_000_000):010}"


def mock_channel_id() -> str:
    return f"C{randrange(0, 10_000_000_000):010}"


def mock_bot_id() -> str:
    return f"B{randrange(0, 10_000_000_000):010}"


def mock_file_id() -> str:
    return f"F{randrange(0, 10_000_000_000):010}"


def random_timestamp() -> int:
    return randrange(1662292800, 1704020400)


def mock_ts() -> str:
    return f"{random_timestamp()}.{randrange(0, 10_000_000):06}"


def mock_message_file(user_id: str, team_id: str) -> dict[str, Any]:
    """A mock file object from a `message:file_share` event."""
    file_id = mock_file_id()
    file_name = "test_file.xml"
    timestamp = random_timestamp()
    return {
        "id": file_id,
        "created": timestamp,
        "timestamp": timestamp,
        "name": file_name,
        "title": file_name,
        "mimetype": "text/plain",
        "filetype": "xml",
        "pretty_type": "XML",
        "user": user_id,
        "editable": True,
        "size": 2254,
        "mode": "snippet",
        "is_external": False,
        "external_type": "",
        "is_public": True,
        "public_url_shared": False,
        "display_as_bot": False,
        "username": "",
        "url_private": f"https://files.slack.com/files-pri/{team_id}-{file_id}/{file_name}",
        "url_private_download": f"https://files.slack.com/files-pri/{team_id}-{file_id}/download/{file_name}",  # noqa: B950
        "permalink": f"https://testworkspace.slack.com/files/{user_id}/{file_id}/{file_name}",
        "permalink_public": f"https://slack-files.com/{team_id}-{file_id}-6325524492",
        "edit_link": f"https://testworkspace.slack.com/files/{user_id}/{file_id}/{file_name}/edit",  # noqa: B950
        "preview": '<?xml version="1.0" encoding="utf-8"?>\nPreview',
        "preview_highlight": "<div>Preview highlight</div>",
        "lines": 35,
        "lines_more": 30,
        "preview_is_truncated": True,
        "has_rich_preview": False,
        "file_access": "visible",
    }


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop():
    """This is required for the async tests to work."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def redis():
    return redis_conn


@pytest.fixture(autouse=True)
def reset_global_cache():
    """Reset the global languages cache before each test to ensure test isolation."""
    global _cached_languages
    original_cache = _cached_languages.copy() if _cached_languages else []
    _cached_languages.clear()
    yield
    # Restore original state after test
    _cached_languages.clear()
    _cached_languages.extend(original_cache)


@pytest.fixture
def user_id() -> str:
    return mock_user_id()


@pytest.fixture
def bot_user_id() -> str:
    return mock_user_id()


@pytest.fixture
def bot_id() -> str:
    return mock_bot_id()


@pytest.fixture
def team_id() -> str:
    return mock_team_id()


@pytest.fixture
def enterprise_id() -> str:
    return mock_enterprise_id()


@pytest.fixture
def app_id() -> str:
    return mock_app_id()


@pytest.fixture
def channel_id() -> str:
    return mock_channel_id()


@pytest.fixture
def ts() -> str:
    return mock_ts()


@pytest.fixture
def ray_client(user_id, team_id, enterprise_id) -> RayClient:
    return RayClient(
        id=str(uuid4()),
        username="test.user",
        user_group_id=str(uuid4()),
        access_token=str(uuid4()),
        slack_user_id=user_id,
        slack_team_id=team_id,
        slack_enterprise_id=enterprise_id,
        slack_access_token=None,
        settings_id=None,
        id_token=str(uuid4()),  # Add id_token for tests that need it
        planname=None,
        sso=False,
    )


@pytest.fixture
def context(user_id, team_id, channel_id) -> AsyncBoltContext:
    return AsyncBoltContext(user_id=user_id, team_id=team_id, channel_id=channel_id)


@pytest.fixture
def message_file(user_id, team_id) -> dict[str, Any]:
    return mock_message_file(user_id, team_id)


@pytest.fixture
def event_body(bot_user_id, user_id, team_id, app_id, channel_id, ts) -> dict[str, Any]:
    return {
        "token": "xxxxxxxxxx",
        "team_id": team_id,
        "api_app_id": app_id,
        "event": {
            "type": "app_home_opened",
            "user": user_id,
            "channel": channel_id,
            "tab": "messages",
            "event_ts": ts,
        },
        "type": "event_callback",
        "event_id": "Ev0000000000",
        "event_time": int(ts.split(".")[0]),
        "authorizations": [
            {
                "enterprise_id": None,
                "team_id": team_id,
                "user_id": bot_user_id,
                "is_bot": True,
                "is_enterprise_install": False,
            }
        ],
        "is_ext_shared_channel": False,
    }


@pytest.fixture
def message_event_body(
    bot_user_id, user_id, team_id, app_id, channel_id, ts
) -> dict[str, Any]:
    return {
        "token": "xxxxxxxxxx",
        "team_id": team_id,
        "api_app_id": app_id,
        "event": {
            "client_msg_id": str(uuid4()),
            "type": "message",
            "text": "Hello",
            "user": user_id,
            "ts": ts,
            "team": team_id,
            "blocks": [
                {
                    "type": "rich_text",
                    "block_id": "moz",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [{"type": "text", "text": "Hello"}],
                        }
                    ],
                }
            ],
            "channel": channel_id,
            "event_ts": ts,
            "channel_type": "im",
        },
        "type": "event_callback",
        "event_id": "Ev0000000000",
        "event_time": int(ts.split(".")[0]),
        "authorizations": [
            {
                "enterprise_id": None,
                "team_id": team_id,
                "user_id": bot_user_id,
                "is_bot": True,
                "is_enterprise_install": False,
            }
        ],
        "is_ext_shared_channel": False,
        "event_context": "4-xxxxxxxxxx",
    }


@pytest.fixture
def file_share_event_body(
    bot_user_id, user_id, team_id, app_id, channel_id, ts, message_file
) -> dict[str, Any]:
    return {
        "token": "xxxxxxxxxx",
        "team_id": team_id,
        "api_app_id": app_id,
        "event": {
            "type": "message",
            "text": "Test file share",
            "files": [message_file],
            "upload": False,
            "user": user_id,
            "display_as_bot": False,
            "ts": ts,
            "blocks": [
                {
                    "type": "rich_text",
                    "block_id": "HG0B",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [{"type": "text", "text": "Test file share"}],
                        }
                    ],
                }
            ],
            "client_msg_id": str(uuid4()),
            "channel": channel_id,
            "subtype": "file_share",
            "event_ts": ts,
            "channel_type": "im",
        },
        "type": "event_callback",
        "event_id": "Ev0000000000",
        "event_time": int(ts.split(".")[0]),
        "authorizations": [
            {
                "enterprise_id": None,
                "team_id": team_id,
                "user_id": bot_user_id,
                "is_bot": True,
                "is_enterprise_install": False,
            }
        ],
        "is_ext_shared_channel": False,
        "event_context": "4-xxxxxxxxxx",
    }


@pytest.fixture
def block_action_body(
    bot_id, bot_user_id, user_id, team_id, app_id, channel_id, ts
) -> dict[str, Any]:
    message_ts = mock_ts()
    return {
        "type": "block_actions",
        "user": {
            "id": user_id,
            "username": "test.user",
            "name": "test.user",
            "team_id": team_id,
        },
        "api_app_id": app_id,
        "token": "xxxxxxxxxx",
        "container": {
            "type": "message",
            "message_ts": message_ts,
            "channel_id": channel_id,
            "is_ephemeral": False,
        },
        "trigger_id": "0000000000.0000000000.xxxxxxxxxx",
        "team": {"id": team_id, "domain": "testworkspace"},
        "enterprise": None,
        "is_enterprise_install": False,
        "channel": {"id": channel_id, "name": "directmessage"},
        "message": {
            "bot_id": bot_id,
            "type": "message",
            "text": "Submit a new translation job",
            "user": bot_user_id,
            "ts": message_ts,
            "app_id": app_id,
            "team": team_id,
            "blocks": [
                {
                    "type": "section",
                    "block_id": "9HUz",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Click here to submit a new translation job",
                        "verbatim": False,
                    },
                },
                {
                    "type": "actions",
                    "block_id": "liSi",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": "new_job",
                            "text": {
                                "type": "plain_text",
                                "text": "New translation job",
                                "emoji": True,
                            },
                            "style": "primary",
                        }
                    ],
                },
            ],
        },
        "state": {"values": {}},
        "response_url": f"https://hooks.slack.com/actions/{team_id}/xxxxxx/xxxxxx",
        "actions": [
            {
                "action_id": "new_job",
                "block_id": "liSi",
                "text": {
                    "type": "plain_text",
                    "text": "New translation job",
                    "emoji": True,
                },
                "style": "primary",
                "type": "button",
                "action_ts": ts,
            }
        ],
    }


@pytest.fixture
def command_body(user_id, team_id, app_id, channel_id) -> dict[str, Any]:
    return {
        "token": "xxxxxxxxxx",
        "team_id": team_id,
        "team_domain": "testworkspace",
        "channel_id": channel_id,
        "channel_name": "directmessage",
        "user_id": user_id,
        "user_name": "test.user",
        "command": "/straker",
        "text": "account",  # text could be missing if no text supplied
        "api_app_id": app_id,
        "is_enterprise_install": "false",
        "response_url": f"https://hooks.slack.com/commands/{team_id}/0000000000/xxxxxxxxxx",
        "trigger_id": "0000000000.0000000000.xxxxxxxxxx",
    }
