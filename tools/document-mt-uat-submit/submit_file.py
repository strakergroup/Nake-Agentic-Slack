#!/usr/bin/env python3
"""Upload a local file into the UAT IBM Straker DM and post the AI Translation button.

The RAY-80512 bot-channel runner cannot submit Document MT: app mentions from
bots are ignored, and slash-command ``translate`` only opens channel settings.
This tool uploads with the Straker UAT bot (``files:write``) then posts the
same ``document_mt_job`` button the app would show after a human file share.
A human still has to click the button and Accept Quote — Slack provides no
``trigger_id`` for bots.

Defaults match the RAY-81323 IBM org-billed Document MT DM submissions.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHANNEL_ID = "D04CQDKNLR2"
DEFAULT_TEAM_ID = "T03PE1PGBV5"
DEFAULT_STRAKER_BOT_USER_ID = "U03U9RB1F0B"
DEFAULT_FILE = Path(
    "/home/wade/Documents/triage/RAY-81323_ibm_tm_exact_v4_en-us_es-es.txt"
)
MYSQL_ALIAS = os.environ.get("DOCUMENT_MT_SUBMIT_MYSQL_ALIAS", "uat-portal")
MYSQL_DB = "ray_integration"
SLACK_ID_RE = re.compile(r"^[A-Z][A-Z0-9]{8,}$")


class SlackClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def auth_test(self) -> dict[str, Any]:
        return self._api("auth.test", {})

    def upload_file(self, channel_id: str, path: Path) -> dict[str, Any]:
        data = path.read_bytes()
        started = self._api(
            "files.getUploadURLExternal",
            {"filename": path.name, "length": str(len(data))},
        )
        upload_url = started.get("upload_url")
        file_id = started.get("file_id")
        if not isinstance(upload_url, str) or not isinstance(file_id, str):
            raise RuntimeError(
                f"files.getUploadURLExternal missing fields: {started!r}"
            )
        self._put_bytes(upload_url, data)
        completed = self._api(
            "files.completeUploadExternal",
            {
                "files": json.dumps([{"id": file_id, "title": path.name}]),
                "channel_id": channel_id,
            },
        )
        files = completed.get("files") or []
        file_info = files[0] if files else {"id": file_id, "title": path.name}
        return {
            "id": file_info.get("id") or file_id,
            "title": file_info.get("title") or path.name,
            "permalink": file_info.get("permalink"),
            "ts": _share_ts(file_info, channel_id),
        }

    def post_ai_translate_button(
        self, channel_id: str, file_info: dict[str, Any], *, thread_ts: str | None
    ) -> str:
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "Please upload your files to translate in the message "
                        "composer below, or alternatively, if you have already "
                        "uploaded your files, click;\n\n"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*AI Translation* - AI translate content from one "
                        "language into multiple languages.\n\n"
                    ),
                },
                "accessory": {
                    "type": "button",
                    "style": "primary",
                    "text": {
                        "type": "plain_text",
                        "emoji": True,
                        "text": "AI Translation",
                    },
                    "action_id": "document_mt_job",
                    "value": json.dumps(
                        {
                            "files": [
                                {"id": file_info["id"], "title": file_info["title"]}
                            ],
                            "channel_id": channel_id,
                        }
                    ),
                },
            },
        ]
        payload: dict[str, Any] = {
            "channel": channel_id,
            "text": "Submit a new job",
            "blocks": json.dumps(blocks),
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts
        posted = self._api("chat.postMessage", payload)
        ts = (posted.get("message") or posted).get("ts")
        if not isinstance(ts, str):
            raise RuntimeError(f"chat.postMessage missing ts: {posted!r}")
        return ts

    def _api(self, method: str, data: dict[str, Any]) -> dict[str, Any]:
        body = urlencode({k: v for k, v in data.items() if v is not None}).encode()
        request = Request(
            f"https://slack.com/api/{method}",
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        return _read_json(request, method)

    def _put_bytes(self, upload_url: str, data: bytes) -> None:
        request = Request(
            upload_url,
            data=data,
            headers={"Content-Type": "application/octet-stream"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=60) as response:
                response.read()
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"Slack file upload failed: {exc}") from exc


def _read_json(request: Request, method: str) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
    except HTTPError as exc:
        raise RuntimeError(f"Slack HTTP error for {method}: {exc}") from exc
    except URLError as exc:
        raise RuntimeError(f"Slack network error for {method}: {exc}") from exc
    if not payload.get("ok"):
        raise RuntimeError(
            f"Slack API {method} failed: {payload.get('error', payload)!r}"
        )
    return payload


def _share_ts(file_info: dict[str, Any], channel_id: str) -> str | None:
    shares = file_info.get("shares") or {}
    for bucket in shares.values():
        if not isinstance(bucket, dict):
            continue
        entries = bucket.get(channel_id) or []
        if entries and isinstance(entries[0], dict):
            ts = entries[0].get("ts")
            if isinstance(ts, str):
                return ts
    return None


def _load_repo_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        env_path = REPO_ROOT / ".env"
        if not env_path.is_file():
            return
        for line in env_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))
        return
    load_dotenv(REPO_ROOT / ".env")


def load_straker_bot_token(team_id: str, bot_user_id: str) -> str:
    token = os.environ.get("STRAKER_BOT_TOKEN", "").strip()
    if token:
        return token
    local = _token_from_local_mysql(team_id, bot_user_id)
    if local:
        print(f"Loaded STRAKER_BOT_TOKEN from local MySQL (bot_user_id={bot_user_id})")
        return local
    uat = _token_from_uat_mysql(team_id, bot_user_id)
    print(f"Loaded STRAKER_BOT_TOKEN from UAT MySQL (bot_user_id={bot_user_id})")
    return uat


def _token_from_local_mysql(team_id: str, bot_user_id: str) -> str | None:
    _load_repo_dotenv()
    try:
        import pymysql
    except ImportError:
        return None
    host = os.environ.get("DB_HOST_ray_integration")
    user = os.environ.get("DB_USER_ray_integration")
    password = os.environ.get("DB_PASSWORD_ray_integration")
    if not all([host, user, password]):
        return None
    conn = pymysql.connect(
        host=host,
        port=int(os.environ.get("DB_PORT_ray_integration", "3306")),
        user=user,
        password=password,
        database=MYSQL_DB,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT bot_token
                FROM slack_bots
                WHERE team_id = %s
                  AND bot_user_id = %s
                  AND bot_token LIKE 'xoxb-%%'
                ORDER BY installed_at DESC
                LIMIT 1
                """,
                (team_id, bot_user_id),
            )
            row = cursor.fetchone()
    finally:
        conn.close()
    token = str(row["bot_token"]).strip() if row and row.get("bot_token") else ""
    return token or None


def _require_slack_id(value: str, label: str) -> str:
    if not SLACK_ID_RE.fullmatch(value):
        raise RuntimeError(f"Invalid {label}: {value!r}")
    return value


def _token_from_uat_mysql(team_id: str, bot_user_id: str) -> str:
    team_id = _require_slack_id(team_id, "team_id")
    bot_user_id = _require_slack_id(bot_user_id, "bot_user_id")
    sql = (
        "SELECT bot_token FROM slack_bots "
        f"WHERE team_id='{team_id}' AND bot_user_id='{bot_user_id}' "
        "AND bot_token LIKE 'xoxb-%' "
        "ORDER BY installed_at DESC LIMIT 1"
    )
    try:
        result = subprocess.run(
            ["mysql-op", MYSQL_ALIAS, MYSQL_DB, "-N", "-B", "-e", sql],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("mysql-op not found and STRAKER_BOT_TOKEN is unset") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"mysql-op query failed:\n{exc.stderr or exc.stdout}"
        ) from exc
    token = (
        result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
    )
    if not token.startswith("xoxb-"):
        raise RuntimeError(
            f"No Straker bot token in UAT MySQL for team {team_id} / {bot_user_id}"
        )
    return token


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument("--channel-id", default=DEFAULT_CHANNEL_ID)
    parser.add_argument("--team-id", default=DEFAULT_TEAM_ID)
    parser.add_argument("--straker-bot-user-id", default=DEFAULT_STRAKER_BOT_USER_ID)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-button", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = args.file.expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"File not found: {path}")

    print(f"file={path}")
    print(f"channel_id={args.channel_id} team_id={args.team_id}")
    if args.dry_run:
        print("dry-run: would upload file and post AI Translation button")
        return 0

    client = SlackClient(load_straker_bot_token(args.team_id, args.straker_bot_user_id))
    identity = client.auth_test()
    print(f"auth.user_id={identity.get('user_id')} auth.team={identity.get('team_id')}")

    uploaded = client.upload_file(args.channel_id, path)
    print(f"uploaded file_id={uploaded['id']} ts={uploaded.get('ts')}")
    if uploaded.get("permalink"):
        print(f"permalink={uploaded['permalink']}")

    if not args.no_button:
        button_ts = client.post_ai_translate_button(
            args.channel_id, uploaded, thread_ts=uploaded.get("ts")
        )
        print(f"posted AI Translation button ts={button_ts}")
        print(
            "Click AI Translation → source English → target Spanish (Spain) → Accept Quote"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
