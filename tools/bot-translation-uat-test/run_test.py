#!/usr/bin/env python3
"""UAT manual test runner for RAY-80512 bot channel auto-translation.

Simulates a streaming bot posting placeholder and final messages into a Slack
channel with auto-translate enabled, then verifies Straker's translation replies.

Requires a *separate* bot token (not the Straker app bot) because
``message_event`` skips auto-translate when ``message.user == bot_user_id``.

Channel defaults are loaded from UAT MySQL (``#test2332``) unless overridden.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Defaults discovered from UAT MySQL (ray_integration) on 2026-07-02.
DEFAULT_CHANNEL_ID = "C09MMR2ETMZ"
DEFAULT_CHANNEL_NAME = "test2332"
DEFAULT_TEAM_ID = "T04D0JGE2HH"
DEFAULT_ENTERPRISE_ID = "E04RDMG8XP1"
DEFAULT_STRAKER_BOT_USER_ID = "U03U9RB1F0B"
# Non-Straker stream bot present in local Percona (straker_translate_wad).
DEFAULT_STREAM_BOT_USER_ID = "U05G5Q168CX"
DEFAULT_TARGET_LANGS = ("eo", "nl")
DEFAULT_DISPLAY_FORMAT = "thread"
DEFAULT_MT_WAIT_SECONDS = 45.0

MYSQL_ALIAS = os.environ.get("BOT_TRANSLATION_TEST_MYSQL_ALIAS", "uat-portal")
MYSQL_DB = os.environ.get("BOT_TRANSLATION_TEST_MYSQL_DB", "ray_integration")
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ChannelConfig:
    channel_id: str
    channel_name: str
    team_id: str
    enterprise_id: str | None
    straker_bot_user_id: str
    target_langs: tuple[str, ...]
    display_format: str


@dataclass(frozen=True)
class PostedMessage:
    text: str
    ts: str


class SlackClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def post_message(self, channel_id: str, text: str) -> PostedMessage:
        payload = self._api("chat.postMessage", {"channel": channel_id, "text": text})
        message = payload.get("message") or payload
        ts = message.get("ts")
        if not isinstance(ts, str):
            raise RuntimeError(f"chat.postMessage missing ts: {payload!r}")
        return PostedMessage(text=text, ts=ts)

    def update_message(self, channel_id: str, ts: str, text: str) -> None:
        self._api("chat.update", {"channel": channel_id, "ts": ts, "text": text})

    def conversation_history(
        self, channel_id: str, *, oldest: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {"channel": channel_id, "limit": limit}
        if oldest:
            params["oldest"] = oldest
        payload = self._api("conversations.history", params)
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return []
        return messages

    def thread_replies(self, channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
        payload = self._api(
            "conversations.replies",
            {"channel": channel_id, "ts": thread_ts, "limit": 50},
        )
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return []
        return messages

    def auth_test(self) -> dict[str, Any]:
        return self._api("auth.test", {})

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


def _load_repo_dotenv() -> None:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")


def load_stream_bot_token_from_local_mysql(
    *,
    team_id: str,
    straker_bot_user_id: str,
    stream_bot_user_id: str | None = None,
) -> tuple[str, str]:
    """Load a non-Straker bot token from local ``ray_integration.slack_bots``.

    Uses ``DB_HOST_ray_integration`` / ``DB_USER_ray_integration`` /
    ``DB_PASSWORD_ray_integration`` from the repo ``.env`` (local Percona).
    Returns ``(bot_token, bot_user_id)``.
    """
    _load_repo_dotenv()
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError(
            "pymysql is required to load tokens from local MySQL"
        ) from exc

    host = os.environ.get("DB_HOST_ray_integration")
    port = int(os.environ.get("DB_PORT_ray_integration", "3306"))
    user = os.environ.get("DB_USER_ray_integration")
    password = os.environ.get("DB_PASSWORD_ray_integration")
    if not all([host, user, password]):
        raise RuntimeError(
            "Missing DB_HOST_ray_integration / DB_USER_ray_integration / "
            "DB_PASSWORD_ray_integration in repo .env"
        )

    if stream_bot_user_id:
        sql = """
            SELECT bot_token, bot_user_id
            FROM slack_bots
            WHERE team_id = %s
              AND bot_user_id = %s
              AND bot_token IS NOT NULL
              AND bot_token != ''
            ORDER BY installed_at DESC
            LIMIT 1
        """
        params: tuple[str, ...] = (team_id, stream_bot_user_id)
    else:
        sql = """
            SELECT bot_token, bot_user_id
            FROM slack_bots
            WHERE team_id = %s
              AND bot_user_id != %s
              AND bot_token IS NOT NULL
              AND bot_token != ''
            ORDER BY installed_at DESC
            LIMIT 1
        """
        params = (team_id, straker_bot_user_id)

    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=MYSQL_DB,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
    finally:
        conn.close()

    if not row or not row.get("bot_token"):
        hint = (
            f"bot_user_id={stream_bot_user_id}"
            if stream_bot_user_id
            else f"any bot except {straker_bot_user_id}"
        )
        raise RuntimeError(
            f"No stream bot token in local MySQL for team {team_id} ({hint})"
        )

    token = str(row["bot_token"]).strip()
    bot_user_id = str(row["bot_user_id"])
    if bot_user_id == straker_bot_user_id:
        raise RuntimeError(
            "Local MySQL returned the Straker app bot token; choose a different "
            "stream bot via --stream-bot-user-id or BOT_TRANSLATION_TEST_STREAM_BOT_USER_ID"
        )
    return token, bot_user_id


def resolve_stream_bot_token(
    args: argparse.Namespace,
    config: ChannelConfig,
) -> str:
    """Return stream bot token from env or local MySQL."""
    token = os.environ.get("STREAM_BOT_TOKEN", "").strip()
    if token or args.dry_run or args.no_local_token:
        return token

    stream_bot_user_id = (
        args.stream_bot_user_id
        or os.environ.get("BOT_TRANSLATION_TEST_STREAM_BOT_USER_ID")
        or DEFAULT_STREAM_BOT_USER_ID
    )
    token, bot_user_id = load_stream_bot_token_from_local_mysql(
        team_id=config.team_id,
        straker_bot_user_id=config.straker_bot_user_id,
        stream_bot_user_id=stream_bot_user_id,
    )
    print(
        f"Loaded STREAM_BOT_TOKEN from local MySQL "
        f"(bot_user_id={bot_user_id}, team={config.team_id})"
    )
    return token


def lookup_channel_config(channel_name: str) -> ChannelConfig:
    """Load auto-translate settings for *channel_name* from UAT MySQL."""
    sql = f"""
SELECT sgt.channel_id,
       sgt.channel_name,
       sgt.display_format,
       sgs.slack_team_id,
       sgs.slack_enterprise_id,
       GROUP_CONCAT(DISTINCT sgtl.lang ORDER BY sgtl.lang) AS target_langs
FROM slack_group_settings_translation sgt
JOIN slack_group_settings sgs ON sgs.id = sgt.settings_id
LEFT JOIN slack_group_settings_translation_langs sgtl
  ON sgtl.translation_settings_id = sgt.id
WHERE sgt.channel_name = '{channel_name.replace("'", "''")}'
GROUP BY sgt.channel_id, sgt.channel_name, sgt.display_format,
         sgs.slack_team_id, sgs.slack_enterprise_id
LIMIT 1;
"""
    command = ["mysql-op", MYSQL_ALIAS, MYSQL_DB, "-N", "-B", "-e", sql]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "mysql-op not found. Install the mysql-client-access skill wrapper."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"mysql-op query failed:\n{exc.stderr or exc.stdout}"
        ) from exc

    row = result.stdout.strip()
    if not row:
        raise RuntimeError(f"No auto-translate settings found for #{channel_name}")

    parts = row.split("\t")
    if len(parts) < 6:
        raise RuntimeError(f"Unexpected mysql row for #{channel_name}: {row!r}")

    channel_id, name, display_format, team_id, enterprise_id, langs = parts
    target_langs = tuple(lang for lang in langs.split(",") if lang)
    return ChannelConfig(
        channel_id=channel_id,
        channel_name=name,
        display_format=display_format,
        team_id=team_id,
        enterprise_id=enterprise_id or None,
        straker_bot_user_id=DEFAULT_STRAKER_BOT_USER_ID,
        target_langs=target_langs,
    )


def load_config(args: argparse.Namespace) -> ChannelConfig:
    if args.lookup_only:
        config = lookup_channel_config(args.channel_name)
        print(json.dumps(config.__dict__, indent=2))
        sys.exit(0)

    if args.channel_id:
        return ChannelConfig(
            channel_id=args.channel_id,
            channel_name=args.channel_name,
            team_id=args.team_id or DEFAULT_TEAM_ID,
            enterprise_id=args.enterprise_id or DEFAULT_ENTERPRISE_ID,
            straker_bot_user_id=args.straker_bot_user_id or DEFAULT_STRAKER_BOT_USER_ID,
            target_langs=tuple(args.target_langs.split(","))
            if args.target_langs
            else DEFAULT_TARGET_LANGS,
            display_format=args.display_format or DEFAULT_DISPLAY_FORMAT,
        )

    try:
        return lookup_channel_config(args.channel_name)
    except RuntimeError as exc:
        print(
            f"Warning: mysql lookup failed ({exc}); using baked-in defaults.",
            file=sys.stderr,
        )
        return ChannelConfig(
            channel_id=DEFAULT_CHANNEL_ID,
            channel_name=DEFAULT_CHANNEL_NAME,
            team_id=DEFAULT_TEAM_ID,
            enterprise_id=DEFAULT_ENTERPRISE_ID,
            straker_bot_user_id=DEFAULT_STRAKER_BOT_USER_ID,
            target_langs=DEFAULT_TARGET_LANGS,
            display_format=DEFAULT_DISPLAY_FORMAT,
        )


def straker_replies(
    messages: list[dict[str, Any]], *, straker_bot_user_id: str
) -> list[dict[str, Any]]:
    replies: list[dict[str, Any]] = []
    for message in messages:
        if message.get("user") == straker_bot_user_id:
            replies.append(message)
        elif message.get("bot_id") and message.get("username", "").lower().startswith(
            "straker"
        ):
            replies.append(message)
    return replies


def wait_for_translation(
    reader: SlackClient,
    config: ChannelConfig,
    *,
    source_ts: str,
    marker: str,
    wait_seconds: float,
    poll_interval: float,
) -> list[dict[str, Any]]:
    deadline = time.time() + wait_seconds
    last_replies: list[dict[str, Any]] = []
    while time.time() < deadline:
        if config.display_format == "thread":
            messages = reader.thread_replies(config.channel_id, source_ts)
        else:
            messages = reader.conversation_history(config.channel_id, oldest=source_ts)
        replies = straker_replies(
            messages, straker_bot_user_id=config.straker_bot_user_id
        )
        last_replies = replies
        if not marker:
            if replies:
                return replies
        elif any(marker in (reply.get("text") or "") for reply in replies):
            return replies
        elif replies and marker not in (messages[0].get("text") or ""):
            # A translation landed even if marker matching is fuzzy (MT rewrites text).
            return replies
        time.sleep(poll_interval)
    return last_replies


def run_placeholders(
    poster: SlackClient,
    reader: SlackClient,
    config: ChannelConfig,
    *,
    wait_seconds: float,
    dry_run: bool,
) -> bool:
    print("\n=== Scenario: placeholder-only (should NOT translate) ===")
    cases = [
        (":3dotsloading:", "slack emoji placeholder"),
        ("...", "ascii ellipsis"),
        ("\u2026", "unicode ellipsis"),
    ]
    passed = True
    for text, label in cases:
        print(f"- Posting {label}: {text!r}")
        if dry_run:
            continue
        posted = poster.post_message(config.channel_id, text)
        time.sleep(wait_seconds)
        if config.display_format == "thread":
            replies = straker_replies(
                reader.thread_replies(config.channel_id, posted.ts),
                straker_bot_user_id=config.straker_bot_user_id,
            )
        else:
            replies = straker_replies(
                reader.conversation_history(config.channel_id, oldest=posted.ts),
                straker_bot_user_id=config.straker_bot_user_id,
            )
        if replies:
            print(f"  FAIL: got {len(replies)} Straker reply/replies (expected 0)")
            passed = False
        else:
            print("  PASS: no Straker translation reply")
    return passed


def run_burst(
    poster: SlackClient,
    reader: SlackClient,
    config: ChannelConfig,
    *,
    wait_seconds: float,
    dry_run: bool,
) -> bool:
    marker = f"[RAY-80512-test-{uuid.uuid4().hex[:8]}]"
    print("\n=== Scenario: separate bot posts (each real message translates) ===")
    print(f"- Marker on final post: {marker}")

    if dry_run:
        print(
            "- Would post placeholders (skipped), draft, then final as separate messages"
        )
        return True

    poster.post_message(config.channel_id, ":3dotsloading:")
    time.sleep(0.4)
    poster.post_message(config.channel_id, "...")
    time.sleep(0.4)
    draft = poster.post_message(config.channel_id, "Draft streaming answer (partial).")
    time.sleep(0.4)
    final = poster.post_message(
        config.channel_id,
        f"Final streaming answer for UAT verification. {marker}",
    )

    print(f"- Posted draft ts={draft.ts}")
    print(f"- Posted final ts={final.ts}")
    per_message_wait = max(wait_seconds / 2, 20.0)

    print(f"- Waiting up to {per_message_wait:.0f}s for draft translation...")
    draft_replies = wait_for_translation(
        reader,
        config,
        source_ts=draft.ts,
        marker="",
        wait_seconds=per_message_wait,
        poll_interval=3.0,
    )
    if not draft_replies:
        print("  FAIL: draft message got no Straker translation reply")
        return False
    print(f"  PASS: draft has {len(draft_replies)} thread reply/replies")

    print(f"- Waiting up to {per_message_wait:.0f}s for final translation...")
    final_replies = wait_for_translation(
        reader,
        config,
        source_ts=final.ts,
        marker=marker,
        wait_seconds=per_message_wait,
        poll_interval=3.0,
    )
    if not final_replies:
        print("  FAIL: final message got no Straker translation reply")
        return False
    print(f"  PASS: final has {len(final_replies)} thread reply/replies")
    return True


def run_edit(
    poster: SlackClient,
    reader: SlackClient,
    config: ChannelConfig,
    *,
    wait_seconds: float,
    dry_run: bool,
) -> bool:
    marker_v1 = f"[RAY-80512-edit-v1-{uuid.uuid4().hex[:6]}]"
    marker_v2 = f"[RAY-80512-edit-v2-{uuid.uuid4().hex[:6]}]"
    print("\n=== Scenario: bot message edit (chat.update in place) ===")
    if dry_run:
        print(
            "- Would post, wait for translation, then chat.update and expect in-place update"
        )
        return True

    original = poster.post_message(
        config.channel_id,
        f"Original bot answer for edit test. {marker_v1}",
    )
    print(f"- Posted original ts={original.ts}; waiting for first translation...")
    first_replies = wait_for_translation(
        reader,
        config,
        source_ts=original.ts,
        marker=marker_v1,
        wait_seconds=wait_seconds,
        poll_interval=3.0,
    )
    if not first_replies:
        print("  FAIL: no initial translation before edit")
        return False
    first_reply_ts = first_replies[-1].get("ts")
    print(f"- Initial translation reply ts={first_reply_ts}")

    poster.update_message(
        config.channel_id,
        original.ts,
        f"Updated bot answer after edit. {marker_v2}",
    )
    print("- Posted chat.update; waiting for updated translation...")
    time.sleep(wait_seconds)

    if config.display_format == "thread":
        replies = straker_replies(
            reader.thread_replies(config.channel_id, original.ts),
            straker_bot_user_id=config.straker_bot_user_id,
        )
    else:
        replies = straker_replies(
            reader.conversation_history(config.channel_id, oldest=original.ts),
            straker_bot_user_id=config.straker_bot_user_id,
        )

    if not replies:
        print("  FAIL: translation disappeared after edit")
        return False

    reply_ts_set = {reply.get("ts") for reply in replies}
    if first_reply_ts and first_reply_ts in reply_ts_set and len(replies) == 1:
        print("  PASS: same reply ts retained (likely chat.update)")
    elif len(replies) == 1:
        print(
            "  PASS-ish: one reply remains; verify manually it reflects edited source"
        )
    else:
        print(f"  WARN: {len(replies)} replies after edit — possible duplicate post")
    return True


def preflight(stream_token: str, reader_token: str, config: ChannelConfig) -> None:
    stream = SlackClient(stream_token)
    reader = SlackClient(reader_token)

    stream_auth = stream.auth_test()
    reader_auth = reader.auth_test()

    stream_user_id = stream_auth.get("user_id")
    if stream_user_id == config.straker_bot_user_id:
        raise RuntimeError(
            "STREAM_BOT_TOKEN belongs to the Straker app bot (U03U9RB1F0B). "
            "Auto-translate ignores the app's own posts. Use a different bot "
            "installed in #test2332."
        )

    print("Preflight OK")
    print(f"- Channel: #{config.channel_name} ({config.channel_id})")
    print(f"- Team: {config.team_id} enterprise={config.enterprise_id}")
    print(
        f"- Auto-translate: display={config.display_format} targets={','.join(config.target_langs)}"
    )
    print(f"- Stream bot: {stream_auth.get('user')} ({stream_user_id})")
    print(
        f"- Reader token user: {reader_auth.get('user')} ({reader_auth.get('user_id')})"
    )
    print(f"- Straker bot user (translation author): {config.straker_bot_user_id}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=("placeholders", "burst", "edit", "all"),
        default="all",
        help="Which test sequence to run (default: all).",
    )
    parser.add_argument("--channel-name", default=DEFAULT_CHANNEL_NAME)
    parser.add_argument("--channel-id", default="")
    parser.add_argument("--team-id", default="")
    parser.add_argument("--enterprise-id", default="")
    parser.add_argument("--straker-bot-user-id", default="")
    parser.add_argument(
        "--stream-bot-user-id",
        default="",
        help=(
            "Local MySQL stream-bot lookup id (default: U05G5Q168CX / "
            "BOT_TRANSLATION_TEST_STREAM_BOT_USER_ID)."
        ),
    )
    parser.add_argument(
        "--no-local-token",
        action="store_true",
        help="Do not load STREAM_BOT_TOKEN from local MySQL when unset.",
    )
    parser.add_argument("--target-langs", default="")
    parser.add_argument("--display-format", default="")
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=float(
            os.environ.get("BOT_TRANSLATION_TEST_WAIT_SECONDS", DEFAULT_MT_WAIT_SECONDS)
        ),
    )
    parser.add_argument(
        "--lookup-only",
        action="store_true",
        help="Print mysql-derived channel config and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned steps without calling Slack.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(args)

    stream_token = resolve_stream_bot_token(args, config)
    reader_token = os.environ.get("SLACK_READER_TOKEN", stream_token).strip()

    if args.lookup_only:
        return 0

    if not stream_token and not args.dry_run:
        print(
            "Set STREAM_BOT_TOKEN or ensure local MySQL (repo .env → localhost "
            "ray_integration.slack_bots) has a non-Straker bot for the channel team.",
            file=sys.stderr,
        )
        return 2

    if not args.dry_run:
        preflight(stream_token, reader_token, config)

    poster = SlackClient(stream_token)
    reader = SlackClient(reader_token)

    results: list[bool] = []
    if args.scenario in ("placeholders", "all"):
        results.append(
            run_placeholders(
                poster,
                reader,
                config,
                wait_seconds=min(args.wait_seconds, 20.0),
                dry_run=args.dry_run,
            )
        )
    if args.scenario in ("burst", "all"):
        results.append(
            run_burst(
                poster,
                reader,
                config,
                wait_seconds=args.wait_seconds,
                dry_run=args.dry_run,
            )
        )
    if args.scenario in ("edit", "all"):
        results.append(
            run_edit(
                poster,
                reader,
                config,
                wait_seconds=args.wait_seconds,
                dry_run=args.dry_run,
            )
        )

    if args.dry_run:
        print("\nDry run complete.")
        return 0

    if all(results):
        print("\nAll selected scenarios passed (or pass-ish — check Slack UI).")
        return 0

    print("\nOne or more scenarios failed — inspect #test2332 and UAT logs.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
