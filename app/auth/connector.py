"""This module contains functions to connect this app to to
other services, e.g. Slack, RAY apps.
"""

import asyncio
import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from ray_logger.slack import SlackAppLog
from slack_bolt.context.async_context import AsyncBoltContext
from slack_sdk.errors import SlackApiError
from slack_sdk.oauth.installation_store import Installation
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import bindparam, text
from straker_auth.languagecloud import (
    create_languagecloud_group_token,
    create_languagecloud_id_token,
)
from straker_utils.sql.async_engine import execute, fetch_all, fetch_one

from ..config import Environment, config, domains
from ..database import async_engines, engines
from ..slack.buglog_notifier import notify_exception

logger = logging.getLogger(__name__)
from .algorithms import encrypt_aes, hash_hmac_sha1


@dataclass(slots=True)
class SlackUser:
    """Dataclass representing a Slack user."""

    user_id: str
    team_id: str
    enterprise_id: str | None
    channel_id: str
    is_subscribed: bool
    bot_token: str
    ray_client_id: str
    ray_username: str
    ray_user_group_id: str | None = None


@dataclass(frozen=True, slots=True)
class SlackOrg:
    """Dataclass representing a Slack user."""

    team_id: str
    enterprise_id: str | None
    bot_token: str
    ray_org_uuid: str
    ray_user_group_id: str | None = None


@dataclass(frozen=True, slots=True)
class RaySuperGroup:
    """Dataclass representing a LanguageCloud super group."""

    id: str
    """The RAY group UUID (`obj_m_group.obj_uuid`)."""
    name: str
    """The name of the group (`obj_m_group.label`)."""
    slack_team_id: str
    """The Verify Organization UUID (`verify_organization.uuid`)."""
    verify_organization_uuid: str
    """The Slack team ID linked to the RAY client."""
    slack_enterprise_id: str | None
    """The Slack enterprise ID linked to the RAY client."""
    enable_verify_in_slack: bool = False
    """The flag to enable verify in Slack (`obj_m_group.enable_verify_in_slack`)."""


@dataclass(frozen=True, slots=True)
class GetCreditBalanceResponse:
    ai_token: int
    mt_token: int


@dataclass(slots=True)
class RayClient:
    """Dataclass representing a LanguageCloud client."""

    id: str
    """The RAY client UUID (`obj_m_member.obj_uuid`)."""
    username: str
    """The RAY client username (`obj_m_member.login`)."""
    user_group_id: str
    """The RAY client default group UUID (`obj_m_member.groupid`)."""
    access_token: str
    """The API token linked to the client."""
    slack_user_id: str
    """The Slack user ID."""
    slack_team_id: str
    """The Slack team ID."""
    slack_enterprise_id: str | None
    """The Slack enterprise ID."""
    slack_access_token: str | None
    """The user's Slack access token."""
    settings_id: int | None
    """The user's Slack settings ID (`ray_integration.slack_user_settings.id`)."""
    id_token: str | None
    """an ID Token according to the OpenID Connect spec"""
    planname: str | None
    """The Slack enterprise ID."""
    sso: bool
    """The SSO flag."""
    is_trial: bool | None = None
    """Whether the effective LanguageCloud session is currently in trial."""
    trial_remaining: int | None = None
    """Days remaining in the current trial, if available."""


@dataclass(frozen=True, slots=True)
class RayConnection:
    """Dataclass representing a connection between a Slack user and a LanguageCloud
    client. This also includes the connection between the Slack workspace and
    the LanguageCloud group.
    """

    super_group: list[RaySuperGroup]
    client: RayClient | None


class RayContext(AsyncBoltContext):
    def __init__(self, context: AsyncBoltContext):
        super().__init__(context)

    @property
    def ray(self) -> Optional[RayConnection]:
        return self.get("ray")

    @property
    def log(self) -> Optional[SlackAppLog]:
        return self.get("log")

    @property
    def is_bot(self) -> bool:
        return self.get("is_bot", False)

    @property
    def user_info(self) -> Optional[Dict[str, Any]]:
        return self.get("user_info")

    @property
    def login_prompt(self) -> Optional[Any]:
        return self.get("login_prompt")


def validate_queue_proxy_secret(secret: str) -> bool:
    """Validates an integration secret key for the queue proxy app.

    Args:
        secret (str): The secret key to validate.

    Returns:
        bool: The validation result.
    """
    return secret == config.slack_queue_proxy_secret.get_secret_value()


def validate_api_callback_signature(
    payload: bytes, api_token: str, signature: str
) -> bool:
    """Validate the HTTP callback request for when a job which was created in
    Slack is updated.
    https://help.strakertranslations.com/hc/en-us/articles/115004089033-Webhooks

    Args:
        payload (bytes): The raw request body.
        api_token (str): The client's API token
        signature (str): The hashed value in the `X-Straker-Signature` header.

    Returns:
        bool: The validation result.
    """
    hash = hash_hmac_sha1(payload, api_token.encode())
    return signature == hash


async def get_bot_token_async(
    team_id: str,
    enterprise_id: str | None = None,
) -> str | None:
    """Gets the Slack bot token for a workspace using async engines."""
    if enterprise_id:
        sql = text(
            """
            SELECT bot_token FROM slack_bots
            WHERE enterprise_id = :enterprise_id
            AND team_id = :team_id
            ORDER BY id DESC
            LIMIT 1
            """
        ).bindparams(enterprise_id=enterprise_id, team_id=team_id)
    else:
        sql = text(
            """
            SELECT bot_token FROM slack_bots
            WHERE team_id = :team_id
            ORDER BY id DESC
            LIMIT 1
            """
        ).bindparams(team_id=team_id)
    result = await fetch_one(sql, async_engines["ray_integration_readonly"])
    return result["bot_token"] if result else None


async def save_user_token_from_installation(
    installation: Installation,
) -> RayClient | None:
    """Saves the Slack user token to a connected LanguageCloud client after a
    successfull Slack app installation. Does nothing if the Slack user is not
    connected to a LanguageCloud account.

    Args:
        installation (Installation): The Slack installation object.

    Returns:
        RayClient | None: The LC client if the Slack user has a connected LC
            account, otherwise `None`.
    """
    if not installation.team_id or not installation.user_id:
        return None
    user = await get_ray_client(
        installation.user_id, installation.team_id, installation.enterprise_id
    )
    if not user:
        return None
    if not installation.user_token or not installation.team_id:
        return user

    scopes_string = (
        ",".join(installation.user_scopes) if installation.user_scopes else None
    )
    if installation.enterprise_id:
        sql = text(
            """
            UPDATE slack_deltaray_link SET
                slack_team_id = :team_id,
                access_token = :access_token,
                access_token_scopes = :access_token_scopes
            WHERE slack_user_id = :user_id
            AND slack_enterprise_id = :enterprise_id
            """
        ).bindparams(
            user_id=installation.user_id,
            team_id=installation.team_id,
            enterprise_id=installation.enterprise_id,
            access_token=installation.user_token,
            access_token_scopes=scopes_string,
        )
    else:
        sql = text(
            """
            UPDATE slack_deltaray_link SET
                access_token = :access_token,
                access_token_scopes = :access_token_scopes
            WHERE slack_user_id = :user_id
            AND slack_team_id = :team_id
            """
        ).bindparams(
            user_id=installation.user_id,
            team_id=installation.team_id,
            access_token=installation.user_token,
            access_token_scopes=scopes_string,
        )
    await execute(sql, async_engines["ray_integration"], commit_after=True)
    return RayClient(
        id=user.id,
        username=user.username,
        access_token=user.access_token,
        slack_user_id=user.slack_user_id,
        slack_team_id=user.slack_team_id,
        slack_enterprise_id=user.slack_enterprise_id,
        slack_access_token=installation.user_token,
        user_group_id=user.user_group_id,
        settings_id=user.settings_id,
        id_token=user.id_token,
        planname=user.planname,
        sso=user.sso,
    )


async def get_slack_org(org_uuid: str, team_id: str | None = None):
    """Gets the Slack organization connected to a RAY client."""
    sql = text(
        """
        SELECT slack_team_id, slack_enterprise_id, verify_organization_uuid, super_group_uuid from slack_super_group_link
        WHERE verify_organization_uuid = :org_uuid
        and is_active = 1
        """
    ).bindparams(org_uuid=org_uuid)
    result = await fetch_one(sql, async_engines["ray_integration"])
    if not result:
        return None
    team_id = team_id or result["slack_team_id"]
    bot_token = await get_bot_token_async(
        team_id=team_id, enterprise_id=result["slack_enterprise_id"]
    )
    if not bot_token:
        return None
    return SlackUser(
        user_id=result["verify_organization_uuid"],
        team_id=team_id,
        enterprise_id=result["slack_enterprise_id"],
        channel_id="",
        is_subscribed=False,
        bot_token=bot_token,
        ray_client_id=org_uuid,
        ray_username="",
        ray_user_group_id=result["super_group_uuid"],
    )


async def get_slack_user_from_workspace_stamps(
    client_id: str,
    *,
    team_id: str,
    slack_user_id: str,
    enterprise_id: str | None = None,
    channel_id: str | None = None,
) -> SlackUser | None:
    """Build a delivery SlackUser from workspace bot token + poster stamps.

    Used when the Verify ``client_id`` has no active deltaray (HT service account
    or deactivated poster link). Bot credentials come from ``slack_bots`` for the
    stamped team/enterprise; notifications go to ``slack_user_id``.
    """
    bot_token = await get_bot_token_async(team_id=team_id, enterprise_id=enterprise_id)
    if not bot_token and enterprise_id:
        # Installation rows are sometimes stored without enterprise_id.
        bot_token = await get_bot_token_async(team_id=team_id, enterprise_id=None)
    if not bot_token:
        return None
    return SlackUser(
        user_id=slack_user_id,
        team_id=team_id,
        enterprise_id=enterprise_id,
        channel_id=channel_id or "",
        is_subscribed=False,
        bot_token=bot_token,
        ray_client_id=client_id,
        ray_username="",
        ray_user_group_id=None,
    )


async def resolve_slack_delivery_user(
    client_id: str,
    *,
    team_id: str | None = None,
    slack_user_id: str | None = None,
    enterprise_id: str | None = None,
    channel_id: str | None = None,
) -> SlackUser | None:
    """Resolve Slack bot credentials for file delivery or event callbacks.

    Member-linked jobs use ``get_slack_user``. Org-billed inline MT (channel,
    shortcut, DM) and Document MT fall back to ``get_slack_org`` and
    optionally override ``user_id`` with the poster's Slack id.

    When member/org lookup fails but the event carries ``team_id`` +
    ``slack_user_id`` (RAY-81247 HT SA / inactive deltaray), resolve the bot
    from the workspace installation and deliver to the stamped poster.

    When ``slack_user_id`` is provided, it always wins over the linked member's
    Slack id (notifications must still go to the poster).
    """
    slack_user = await get_slack_user(client_id, team_id)
    if slack_user is None:
        slack_user = await get_slack_org(client_id, team_id)
    if slack_user is None and team_id and slack_user_id:
        slack_user = await get_slack_user_from_workspace_stamps(
            client_id,
            team_id=team_id,
            slack_user_id=slack_user_id,
            enterprise_id=enterprise_id,
            channel_id=channel_id,
        )
    if slack_user is None:
        return None
    if slack_user_id:
        slack_user.user_id = slack_user_id
    if channel_id and not slack_user.channel_id:
        slack_user.channel_id = channel_id
    return slack_user


async def get_slack_user(ray_client_id: str, team_id: str | None = None):
    """Gets the Slack user connected to a RAY client.

    Args:
        ray_client_id (str): The LanguageCloud user ID.
    """
    sql = text(
        """
        SELECT link.slack_user_id,link.slack_team_id,link.slack_enterprise_id,
            link.slack_channel_id,link.is_subscribed,mem.login,mem.groupid
        FROM slack_deltaray_link link
        INNER JOIN sitemanager.obj_m_member mem
        ON link.member_uuid = mem.obj_uuid
        WHERE link.member_uuid = :member_uuid
        AND link.is_active = 1
        AND mem.active = 1
        AND mem.is_deleted = 0
        LIMIT 1
        """
    ).bindparams(member_uuid=ray_client_id)
    result = await fetch_one(sql, async_engines["ray_integration"])
    if result:
        team_id = team_id or result["slack_team_id"]
        bot_token = await get_bot_token_async(
            team_id=team_id, enterprise_id=result["slack_enterprise_id"]
        )
        if bot_token:
            return SlackUser(
                user_id=result["slack_user_id"],
                team_id=team_id,
                enterprise_id=result["slack_enterprise_id"],
                channel_id=result["slack_channel_id"],
                is_subscribed=bool(result["is_subscribed"]),
                bot_token=bot_token,
                ray_client_id=ray_client_id,
                ray_username=result["login"],
                ray_user_group_id=result["groupid"],
            )
    return None


async def get_demo_link(member_uuid: str):
    sql = text(
        """
        SELECT member_uuid
        FROM slack_demo_users
        WHERE member_uuid = :member_uuid
        """
    ).bindparams(member_uuid=member_uuid)
    result = await fetch_one(sql, async_engines["ray_integration_readonly"])
    if result:
        sql = text(
            """
            SELECT link.slack_user_id
            FROM slack_demo_link link
            """
        )
        demo_results = await fetch_all(sql, async_engines["ray_integration_readonly"])
        slack_user_ids: list[str] = []
        for row in demo_results:
            if hasattr(row, "get"):
                slack_user_id = row.get("slack_user_id")
                if slack_user_id:
                    slack_user_ids.append(slack_user_id)
        return slack_user_ids
    return []


async def get_client_access_tokens(ray_client_id: str):
    """Gets all the active API access tokens of a RAY client."""
    sql = text(
        """
        SELECT obj_uuid FROM access_token
        WHERE account_id = :client_id
        AND active = 1
        """
    ).bindparams(client_id=ray_client_id)
    result = await fetch_all(sql, async_engines["api_readonly"])
    return tuple(row["obj_uuid"] for row in result)


async def get_demo_super_group(
    team_id: str, enterprise_id: str | None
) -> list[RaySuperGroup] | None:
    sql = text(
        """
        SELECT link.super_group_uuid, g.label, g.enable_verify_in_slack
        FROM slack_super_group_link link
        INNER JOIN sitemanager.obj_m_group g
        ON link.super_group_uuid = g.obj_uuid
        INNER JOIN slack_deltaray_link dlink
        ON dlink.slack_enterprise_id = link.slack_enterprise_id
        INNER JOIN slack_demo_users dmem on dmem.member_uuid = dlink.member_uuid
        WHERE link.is_active = 1
        AND link.slack_enterprise_id = :enterprise_id
        LIMIT 1
        """
    ).bindparams(enterprise_id=enterprise_id)
    result = await fetch_one(sql, async_engines["ray_integration_readonly"])
    if not result:
        return None
    return [
        RaySuperGroup(
            id=result["super_group_uuid"],
            name=result["label"],
            slack_team_id=team_id,
            slack_enterprise_id=enterprise_id,
            enable_verify_in_slack=bool(result["enable_verify_in_slack"]),
            verify_organization_uuid="",
        )
    ]


async def get_ray_super_group(
    team_id: str, enterprise_id: str | None = None
) -> list[RaySuperGroup] | None:
    """Gets the LanguageCloud super group linked to the Slack workspace if an active
    link exists, otherwise returns None.

    Args:
        team_id (str): The ID of the team.
    """
    if enterprise_id:
        sql = text(
            """
            SELECT link.super_group_uuid, g.label, vo.organization_name, g.enable_verify_in_slack, vo.obj_uuid AS verify_organization_id
            FROM slack_super_group_link link
            INNER JOIN sitemanager.obj_m_group g
            ON link.super_group_uuid = g.obj_uuid
            INNER JOIN sitemanager.verify_organization vo
            ON vo.obj_uuid = link.verify_organization_uuid
            WHERE link.slack_enterprise_id = :enterprise_id
            AND link.is_active = 1
            """
        ).bindparams(enterprise_id=enterprise_id)
    else:
        sql = text(
            """
            SELECT link.super_group_uuid, g.label, vo.organization_name, g.enable_verify_in_slack, vo.obj_uuid AS verify_organization_id
            FROM slack_super_group_link link
            INNER JOIN sitemanager.obj_m_group g
            ON link.super_group_uuid = g.obj_uuid
            INNER JOIN sitemanager.verify_organization vo
            ON vo.obj_uuid = link.verify_organization_uuid
            WHERE link.slack_team_id = :team_id
            AND link.is_active = 1
            """
        ).bindparams(team_id=team_id)

    rows = await fetch_all(sql, async_engines["ray_integration_readonly"])
    if not rows:
        return None

    return [
        RaySuperGroup(
            id=row["super_group_uuid"],
            name=row["organization_name"],
            slack_team_id=team_id,
            slack_enterprise_id=enterprise_id,
            enable_verify_in_slack=bool(row["enable_verify_in_slack"]),
            verify_organization_uuid=row["verify_organization_id"],
        )
        for row in rows
    ]


# Real IBM customer workspaces — HT service account + email→CRM identity.
IBM_CUSTOMER_SUPER_GROUP_UUIDS = frozenset(
    {
        "9ADE9F44-92A4-4EEE-9BCC-96AFEF9B6D36",  # IBM Supergroup 2021
    }
)

# Internal workspaces treated as IBM for product UI only (not HT unauthenticated).
# Straker Dev / sandbox must still require LanguageCloud account connection.
IBM_INTERNAL_TEST_SUPER_GROUP_UUIDS = frozenset(
    {
        "13D8D894-3DC5-49DC-9DD0-AD9EA537E597",  # Straker Developer Super Group
        "94c8dd41-9029-4aae-883a-57e4b86ead17",  # Test Straker Sandbox (prod)
        "7f8bcd96-3856-43d6-a01e-3d4c4c196558",  # Test Straker Sandbox (UAT)
    }
)

IBM_LIKE_SUPER_GROUP_UUIDS = (
    IBM_CUSTOMER_SUPER_GROUP_UUIDS | IBM_INTERNAL_TEST_SUPER_GROUP_UUIDS
)


def _enterprise_has_active_ibm_like_super_group(
    enterprise_id: str,
    *,
    super_group_uuids: frozenset[str],
) -> bool:
    placeholders = ", ".join(f"'{uuid}'" for uuid in sorted(super_group_uuids))
    with engines["ray_integration_readonly"].connect() as conn:
        sql = text(
            f"""
            SELECT link.super_group_uuid
            FROM slack_super_group_link link
            INNER JOIN sitemanager.obj_m_group g
            ON link.super_group_uuid = g.obj_uuid
            WHERE link.slack_enterprise_id = :enterprise_id
            AND link.is_active = 1
            AND link.super_group_uuid IN ({placeholders})
            AND link.verify_organization_uuid is not null
            LIMIT 1
            """
        ).bindparams(enterprise_id=enterprise_id)
        result = conn.execute(sql)
        return result.first() is not None


def is_ibm_super_group(
    enterprise_id: str | None = None,
) -> bool:
    """True when the Slack enterprise is linked to any IBM-like super group.

    Includes Straker Dev / sandbox (IBM UI testing). For HT service-account and
    email→CRM auto-identity use ``is_ibm_customer_super_group`` instead.
    """
    if not enterprise_id:
        return False
    return _enterprise_has_active_ibm_like_super_group(
        enterprise_id, super_group_uuids=IBM_LIKE_SUPER_GROUP_UUIDS
    )


def is_ibm_customer_super_group(
    enterprise_id: str | None = None,
) -> bool:
    """True only for real IBM customer enterprises (not Straker Dev / sandbox).

    Gates HT service-account ownership and Slack-email→CRM auto-resolve so
    internal IBM-like test workspaces still require LanguageCloud connection.
    """
    if not enterprise_id:
        return False
    return _enterprise_has_active_ibm_like_super_group(
        enterprise_id, super_group_uuids=IBM_CUSTOMER_SUPER_GROUP_UUIDS
    )


async def get_ray_demo_client(
    user_id: str, team_id: str, slack_enterprise_id: str | None
) -> RayClient | None:
    sql = text(
        """
            SELECT id
            FROM slack_demo_link link
            WHERE slack_user_id = :user_id
            """
    ).bindparams(user_id=user_id)
    result = await fetch_one(sql, async_engines["ray_integration_readonly"])
    if not result:
        return None

    sql = text(
        """
        SELECT link.member_uuid, link.slack_enterprise_id, mem.login, mem.email_primary, mem.given_name, mem.family_name,
        mem.active, mem.groupid, link.access_token, settings.id AS settings_id
        FROM slack_deltaray_link link
        INNER JOIN sitemanager.obj_m_member mem
        ON link.member_uuid = mem.obj_uuid
        INNER JOIN slack_demo_users dmem
        ON dmem.member_uuid = link.member_uuid
        LEFT JOIN slack_user_settings settings
        ON link.member_uuid = settings.member_uuid
        WHERE mem.active = 1
        AND link.slack_enterprise_id = :slack_enterprise_id
        AND mem.is_deleted = 0
        LIMIT 1
        """
    ).bindparams(slack_enterprise_id=slack_enterprise_id)
    row = await fetch_one(sql, async_engines["ray_integration_readonly"])
    if not row:
        return None

    (
        ray_client_id,
        user_group_id,
        username,
        slack_enterprise_id,
        slack_access_token,
        settings_id,
    ) = (
        row["member_uuid"],
        row["groupid"],
        row["login"],
        row["slack_enterprise_id"],
        row["access_token"],
        row["settings_id"],
    )
    id_token = create_languagecloud_id_token(
        uuid=ray_client_id,
        given_name=row["given_name"],
        family_name=row["family_name"],
        email=row["email_primary"],
        is_active=bool(row["active"]),
        aud="languagecloud-api",
        secret=config.languagecloud_api_key.get_secret_value(),
    )
    # Now get the access token for authentication.
    sql = text(
        """
        SELECT obj_uuid FROM access_token
        WHERE account_id = :client_id
        AND active = 1
        LIMIT 1
        """
    ).bindparams(client_id=ray_client_id)
    access_token_result = await fetch_one(sql, async_engines["api_readonly"])
    if not access_token_result:
        return None
    access_token = access_token_result["obj_uuid"]
    return RayClient(
        id=ray_client_id,
        user_group_id=user_group_id,
        username=username,
        access_token=access_token,
        slack_user_id=user_id,
        slack_team_id=team_id,
        slack_enterprise_id=slack_enterprise_id,
        slack_access_token=slack_access_token,
        settings_id=settings_id,
        id_token=id_token,
        planname=None,
        sso=False,
    )


async def _slack_email_from_details(user_id: str) -> str:
    """Cached Slack profile email from ``slack_user_details``."""
    sql = text(
        """
        SELECT client_email
        FROM slack_user_details
        WHERE slack_user_id = :user_id
          AND client_email IS NOT NULL
          AND client_email != ''
        ORDER BY updated_at DESC
        LIMIT 1
        """
    ).bindparams(user_id=user_id)
    row = await fetch_one(sql, async_engines["ray_integration_readonly"])
    return ((row or {}).get("client_email") or "").strip()


async def _slack_email_from_api(
    user_id: str, team_id: str, enterprise_id: str | None
) -> str:
    """Live Slack profile email via bot ``users_info``."""
    bot_token = await get_bot_token_async(team_id=team_id, enterprise_id=enterprise_id)
    if not bot_token:
        return ""
    try:
        client = AsyncWebClient(token=bot_token)
        user_info = await client.users_info(user=user_id)
        profile = ((user_info or {}).get("user") or {}).get("profile") or {}
        return (profile.get("email") or "").strip()
    except Exception:
        logger.warning(
            "Failed to resolve Slack profile email for IBM CRM lookup",
            extra={"slack_user_id": user_id, "slack_team_id": team_id},
            exc_info=True,
        )
        return ""


async def resolve_slack_user_email_candidates(
    user_id: str, team_id: str, enterprise_id: str | None
) -> list[str]:
    """Unique Slack emails for CRM lookup (live API first, then cached details).

    Cached ``slack_user_details`` can be stale (old domain). Prefer the live
    Slack profile, then fall back to cache so either source can match CRM.
    """
    candidates: list[str] = []
    seen: set[str] = set()
    for email in (
        await _slack_email_from_api(user_id, team_id, enterprise_id),
        await _slack_email_from_details(user_id),
    ):
        normalized = (email or "").strip()
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            candidates.append(normalized)
    return candidates


async def resolve_slack_user_email(
    user_id: str, team_id: str, enterprise_id: str | None
) -> str:
    """Best-effort Slack user email (live API first, then cached details)."""
    candidates = await resolve_slack_user_email_candidates(
        user_id, team_id, enterprise_id
    )
    return candidates[0] if candidates else ""


async def get_active_crm_member_by_email(email: str) -> dict[str, Any] | None:
    """Active CRM ``obj_m_member`` row for login/email_primary (case-insensitive)."""
    email = (email or "").strip()
    if not email:
        return None
    sql = text(
        """
        SELECT
            m.obj_uuid AS member_uuid,
            m.login,
            m.email_primary,
            m.given_name,
            m.family_name,
            m.active,
            m.groupid,
            settings.id AS settings_id
        FROM sitemanager.obj_m_member AS m
        LEFT JOIN slack_user_settings AS settings
            ON m.obj_uuid = settings.member_uuid
        WHERE m.is_deleted = 0
          AND m.active = 1
          AND (
              LOWER(m.login) = LOWER(:email)
              OR LOWER(m.email_primary) = LOWER(:email)
          )
        LIMIT 1
        """
    ).bindparams(email=email)
    return await fetch_one(sql, async_engines["ray_integration_readonly"])


async def ensure_active_ibm_deltaray_link(
    *,
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
    member_uuid: str,
    channel_id: str | None = None,
) -> None:
    """Upsert an active Slack↔CRM link after email-based IBM identity resolve."""
    channel_id = channel_id or user_id
    find_sql = text(
        """
        SELECT id
        FROM slack_deltaray_link
        WHERE slack_user_id = :user_id
          AND (
              slack_enterprise_id = :enterprise_id
              OR slack_team_id = :team_id
              OR :enterprise_id IS NULL
          )
        LIMIT 1
        """
    ).bindparams(
        user_id=user_id,
        team_id=team_id,
        enterprise_id=enterprise_id,
    )
    existing = await fetch_one(find_sql, async_engines["ray_integration"])
    if existing:
        update_sql = text(
            """
            UPDATE slack_deltaray_link
            SET
                member_uuid = :member_uuid,
                slack_team_id = :team_id,
                slack_enterprise_id = :enterprise_id,
                slack_channel_id = :channel_id,
                is_subscribed = 1,
                is_active = 1,
                activated_at = NOW(),
                deactivated_at = NULL
            WHERE slack_user_id = :user_id
              AND (
                  slack_enterprise_id = :enterprise_id
                  OR slack_team_id = :team_id
                  OR :enterprise_id IS NULL
              )
            """
        ).bindparams(
            member_uuid=member_uuid,
            user_id=user_id,
            team_id=team_id,
            enterprise_id=enterprise_id,
            channel_id=channel_id,
        )
        await execute(update_sql, async_engines["ray_integration"], commit_after=True)
        return

    insert_sql = text(
        """
        INSERT INTO slack_deltaray_link (
            member_uuid,
            slack_user_id,
            slack_team_id,
            slack_enterprise_id,
            slack_app_id,
            slack_channel_id,
            is_subscribed,
            is_active,
            is_sso,
            activated_at
        ) VALUES (
            :member_uuid,
            :user_id,
            :team_id,
            :enterprise_id,
            '',
            :channel_id,
            1,
            1,
            1,
            NOW()
        )
        """
    ).bindparams(
        member_uuid=member_uuid,
        user_id=user_id,
        team_id=team_id,
        enterprise_id=enterprise_id,
        channel_id=channel_id,
    )
    await execute(insert_sql, async_engines["ray_integration"], commit_after=True)


async def get_ray_client_ibm_by_email(
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
) -> RayClient | None:
    """Resolve IBM Slack identity from Slack email → active CRM member.

    Does not require an existing ``slack_deltaray_link``. When a member is found,
    an active deltaray row is upserted so delivery callbacks keep working.

    Tries live Slack profile email first, then cached ``slack_user_details``, so
    a stale cached domain (e.g. ``@strakertranslations.com``) cannot block an
    active CRM match on the current Slack email (e.g. ``@strakergroup.com``).
    """
    member: dict[str, Any] | None = None
    matched_email = ""
    for email in await resolve_slack_user_email_candidates(
        user_id, team_id, enterprise_id
    ):
        member = await get_active_crm_member_by_email(email)
        if member:
            matched_email = email
            break
    if not member:
        return None

    await ensure_active_ibm_deltaray_link(
        user_id=user_id,
        team_id=team_id,
        enterprise_id=enterprise_id,
        member_uuid=member["member_uuid"],
        channel_id=user_id,
    )
    logger.info(
        "Resolved IBM Slack user to CRM member by email",
        extra={
            "slack_user_id": user_id,
            "slack_team_id": team_id,
            "member_uuid": member["member_uuid"],
        },
    )

    id_token = create_languagecloud_id_token(
        uuid=member["member_uuid"],
        given_name=member["given_name"] or "",
        family_name=member["family_name"] or "",
        email=member["email_primary"] or matched_email,
        is_active=bool(member["active"]),
        aud="languagecloud-api",
        secret=config.languagecloud_api_key.get_secret_value(),
    )
    access_token_result = await fetch_one(
        text(
            """
            SELECT obj_uuid FROM access_token
            WHERE account_id = :client_id
            AND active = 1
            LIMIT 1
            """
        ).bindparams(client_id=member["member_uuid"]),
        async_engines["api"],
    )
    return RayClient(
        id=member["member_uuid"],
        user_group_id=member["groupid"] or "",
        username=member["login"] or member["email_primary"] or matched_email,
        access_token=(access_token_result or {}).get("obj_uuid") or "",
        slack_user_id=user_id,
        slack_team_id=team_id,
        slack_enterprise_id=enterprise_id,
        slack_access_token=None,
        settings_id=member.get("settings_id"),
        id_token=id_token,
        planname=None,
        sso=True,
    )


async def get_ray_client(
    user_id: str, team_id: str, enterprise_id: str | None = None
) -> RayClient | None:
    """Gets the LanguageCloud client id and username linked to the Slack account if an active
    link exists, otherwise returns None.

    On IBM enterprises, identity is resolved from the Slack user's email to an
    active CRM member (RAY-81247) — ``slack_deltaray_link`` is not required.
    When email matches, an active deltaray row is upserted. Active deltaray is
    still used as a fallback when email cannot be resolved.

    Args:
        user_id (str): The Slack user ID.
        team_id (str): The Slack team ID.
        enterprise_id (str | None): The Slack enterprise ID.
    """
    # Lazy import avoids connector ↔ ray.utils cycle at module load.
    from app.ray.utils import is_ibm_customer_enterprise

    if is_ibm_customer_enterprise(enterprise_id):
        ibm_client = await get_ray_client_ibm_by_email(user_id, team_id, enterprise_id)
        if ibm_client is not None:
            return ibm_client

    # First find the client details.
    if enterprise_id:
        sql = text(
            """
            SELECT link.member_uuid, mem.login, mem.email_primary, mem.given_name, mem.family_name, link.slack_team_id,
            mem.active, mem.groupid, link.is_sso, link.access_token, settings.id AS settings_id
            FROM slack_deltaray_link link
            INNER JOIN sitemanager.obj_m_member mem
            ON link.member_uuid = mem.obj_uuid
            LEFT JOIN slack_user_settings settings
            ON link.member_uuid = settings.member_uuid
            WHERE link.slack_user_id = :user_id
            AND link.slack_enterprise_id = :enterprise_id
            AND link.is_active = 1
            AND mem.active = 1
            AND mem.is_deleted = 0
            LIMIT 1
            """
        ).bindparams(user_id=user_id, enterprise_id=enterprise_id)
    else:
        sql = text(
            """
            SELECT link.member_uuid, mem.login, mem.email_primary, mem.given_name, mem.family_name, link.slack_team_id,
            mem.active, mem.groupid, link.is_sso, link.access_token, settings.id AS settings_id
            FROM slack_deltaray_link link
            INNER JOIN sitemanager.obj_m_member mem
            ON link.member_uuid = mem.obj_uuid
            LEFT JOIN slack_user_settings settings
            ON link.member_uuid = settings.member_uuid
            WHERE link.slack_user_id = :user_id
            AND link.slack_team_id = :team_id
            AND link.is_active = 1
            AND mem.active = 1
            AND mem.is_deleted = 0
            LIMIT 1
            """
        ).bindparams(user_id=user_id, team_id=team_id)

    row = await fetch_one(sql, async_engines["ray_integration"])
    if not row:
        return None

    if row["slack_team_id"] != team_id:
        # update slack_team_id of link to team_id
        sql = text(
            """
            UPDATE slack_deltaray_link SET
                slack_team_id = :team_id
            WHERE slack_user_id = :user_id
            AND slack_enterprise_id = :enterprise_id
            AND is_active = 1
            """
        ).bindparams(user_id=user_id, team_id=team_id, enterprise_id=enterprise_id)
        await execute(sql, async_engines["ray_integration"], commit_after=True)

    id_token = create_languagecloud_id_token(
        uuid=row["member_uuid"],
        given_name=row["given_name"],
        family_name=row["family_name"],
        email=row["email_primary"],
        is_active=bool(row["active"]),
        aud="languagecloud-api",
        secret=config.languagecloud_api_key.get_secret_value(),
    )
    (
        ray_client_id,
        user_group_id,
        username,
        is_sso,
        slack_access_token,
        settings_id,
    ) = (
        row["member_uuid"],
        row["groupid"],
        row["login"],
        row["is_sso"],
        row["access_token"],
        row["settings_id"],
    )
    # Now get the access token for authentication.
    sql = text(
        """
        SELECT obj_uuid FROM access_token
        WHERE account_id = :client_id
        AND active = 1
        LIMIT 1
        """
    ).bindparams(client_id=ray_client_id)
    access_token_result = await fetch_one(sql, async_engines["api"])
    if not access_token_result:
        access_token = ""
    else:
        access_token = access_token_result["obj_uuid"]
    return RayClient(
        id=ray_client_id,
        user_group_id=user_group_id,
        username=username,
        access_token=access_token,
        slack_user_id=user_id,
        slack_team_id=team_id,
        slack_enterprise_id=enterprise_id,
        slack_access_token=slack_access_token,
        settings_id=settings_id,
        id_token=id_token,
        planname=None,
        sso=is_sso,
    )


async def get_ray_connection(
    user_id: str, team_id: str, enterprise_id: str | None
) -> RayConnection | None:
    """Gets the LanguageCloud super group and client linked to the Slack workspace
    and user. If the Slack workspace is not linked, ignore the Slack user link.
    A Slack workspace can have a connection without a Slack user connection.
    """
    super_group, client = await asyncio.gather(
        get_ray_super_group(team_id, enterprise_id),
        get_ray_client(user_id, team_id, enterprise_id),
    )
    if super_group is None:
        return None

    return RayConnection(super_group, client)


async def get_ray_connection_demo(
    user_id: str, team_id: str, enterprise_id: str | None
) -> RayConnection | None:
    """Gets the LanguageCloud super group and client linked to the Slack workspace
    and user. If the Slack workspace is not linked, ignore the Slack user link.
    A Slack workspace can have a connection without a Slack user connection.
    """
    super_group, client = await asyncio.gather(
        get_demo_super_group(team_id, enterprise_id),
        get_ray_demo_client(user_id, team_id, enterprise_id),
    )
    if super_group is None:
        return None
    return RayConnection(super_group, client)


async def get_group_admin_slack_users(group_id: str):
    """Gets the Slack users of the admins of a LanguageCloud group."""
    sql = text(
        """
        SELECT slack.slack_user_id, slack.slack_team_id, slack.slack_enterprise_id,
            slack.slack_channel_id, slack.is_subscribed, bots.bot_token,
            mem.obj_uuid, mem.login
        FROM obj_m_mglink link
        INNER JOIN obj_m_member mem
        ON link.memberid = mem.obj_uuid
        INNER JOIN ray_integration.slack_deltaray_link slack
        ON link.memberid = slack.member_uuid
        INNER JOIN ray_integration.slack_bots bots
        ON bots.id = (
            SELECT id FROM ray_integration.slack_bots bots2
            WHERE bots2.team_id = slack.slack_team_id
            OR bots2.enterprise_id = slack.slack_enterprise_id
            ORDER BY id DESC LIMIT 1
        )
        WHERE link.groupid = :group_id
        AND link.client_type IN ('Admin', 'Owner')
        AND mem.active = 1
        AND mem.is_deleted = 0
        AND slack.is_active = 1
        GROUP BY link.memberid
        """
    ).bindparams(group_id=group_id)
    result = await fetch_all(sql, async_engines["sitemanager_readonly"])

    slack_users = []
    for row in result:
        slack_users.append(
            SlackUser(
                user_id=row["slack_user_id"],
                team_id=row["slack_team_id"],
                enterprise_id=row["slack_enterprise_id"],
                channel_id=row["slack_channel_id"],
                is_subscribed=bool(row["is_subscribed"]),
                bot_token=row["bot_token"],
                ray_client_id=row["obj_uuid"],
                ray_username=row["login"],
            )
        )
    return slack_users


async def connect_ray_account(
    user_id: str,
    team_id: str,
    enterprise_id: str | None = None,
    channel_id: str | None = None,
) -> str:
    """Connect the LanguageCloud account of a slack user.

    Args:
        user_id (str): The Slack user ID.
        team_id (str): The Slack team ID.
        enterprise_id (str): The Slack enterprise ID.

    Returns:
        bool: The Slack user had a connected LanguageCloud account.
    """

    try:
        channel_id = channel_id or user_id
        url = get_language_cloud_connect_url(
            user_id, team_id, enterprise_id, channel_id
        )
        async with httpx.AsyncClient(timeout=10) as http:
            await http.post(url)
        return "success"
    except Exception as e:
        notify_exception(e)
        return "failed"


def disconnect_ray_account(
    user_id: str, team_id: str, enterprise_id: str | None = None
) -> bool:
    """Disconnect the LanguageCloud account of a slack user.

    Args:
        user_id (str): The Slack user ID.
        team_id (str): The Slack team ID.
        enterprise_id (str): The Slack enterprise ID.

    Returns:
        bool: The Slack user had a connected LanguageCloud account.
    """
    with engines["ray_integration"].begin() as conn:
        sql = text(
            """
            UPDATE slack_deltaray_link SET
                is_active = 0,
                deactivated_at = NOW()
            WHERE slack_user_id = :user_id
            AND (
                slack_team_id = :team_id
                OR slack_enterprise_id = :enterprise_id
            )
            AND is_active = 1
            """
        ).bindparams(user_id=user_id, team_id=team_id, enterprise_id=enterprise_id)
        result = conn.execute(sql)
    return result.rowcount > 0


def disconnect_ray_super_group_and_users(
    team_id: str, enterprise_id: str | None = None
) -> bool:
    """Disconnect the LanguageCloud super group and all connected users
    of a Slack Workspace.

    Args:
        team_id (str): The Slack team ID.
        enterprise_id (str | None): The Slack enterprise ID.

    Returns:
        bool: An active Slack-LanguageCloud connection was deactivated.
    """
    with engines["ray_integration"].begin() as conn:
        sql = text(
            """
            UPDATE slack_super_group_link SET
                is_active = 0,
                deactivated_at = NOW()
            WHERE (
                slack_team_id = :team_id
                OR slack_enterprise_id = :enterprise_id
            )
            AND is_active = 1
            """
        ).bindparams(team_id=team_id, enterprise_id=enterprise_id)
        result1 = conn.execute(sql)
        sql = text(
            """
            UPDATE slack_deltaray_link SET
                is_active = 0,
                deactivated_at = NOW()
            WHERE (
                slack_team_id = :team_id
                OR slack_enterprise_id = :enterprise_id
            )
            AND is_active = 1
            """
        ).bindparams(team_id=team_id, enterprise_id=enterprise_id)
        result2 = conn.execute(sql)
    return result1.rowcount > 0 or result2.rowcount > 0


def encrpyt_slack_integration_token(
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
    channel_id: str,
    expire_seconds: int = 7200,
) -> str:
    """Generates time-sensitive token to allow the Slack app to communicate
    with the RAY platform securely.

    Args:
        user_id (str): The Slack user ID.
        team_id (str): The Slack team ID.
        enterprise_id (str | None): The Slack enterprise ID.
        channel_id (str): The ID of channel where the login command was called.
        expire_seconds (int, optional): The time in seconds before the token expires.
        Defaults to 7200.

    Returns:
        str: The encrypted token.
    """
    epoch = int(time.time())
    data = {
        "userId": user_id,
        "teamId": team_id,
        "enterpriseId": enterprise_id or None,
        "channelId": channel_id,
        "clientId": config.slack_client_id,
        "created": epoch,
        "expires": epoch + expire_seconds,
    }

    return encrypt_aes(json.dumps(data), config.slack_deltaray_key.get_secret_value())


def get_language_cloud_connect_url(
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
    channel_id: str,
    expire_seconds: int = 7200,
) -> str:
    """Generates a URL for a user to connect their Slack account to their
    LanguageCloud account.

    Args:
        user_id (str): The Slack user ID.
        team_id (str): The Slack team ID.
        enterprise_id (str | None): The Slack enterprise ID.
        channel_id (str): The ID of channel where the login command was called.
        expire_seconds (int, optional): The time in seconds before the token expires.
        Defaults to 7200.

    Returns:
        str: The URL to connect a user's Slack account and LanguageCloud account.
    """
    params = {
        "slack_token": encrpyt_slack_integration_token(
            user_id, team_id, enterprise_id, channel_id, expire_seconds
        )
    }
    # TODO: link to apps page
    return f"{domains.verify}/integrations?{urlencode(params)}"


async def approve_pending_groups(
    admin_client_id: str, pending_client_id: str, pending_client_username: str
) -> tuple[str, ...]:
    """Approve the pending groups of a new client that the client is an admin of.
    This function will be moved to a REST API in the future.
    """
    with engines["sitemanager_readonly"].connect() as conn:
        # First get the groups that the client is an admin of.
        sql = text(
            """
            SELECT DISTINCT groupid
            FROM obj_m_mglink
            WHERE memberid = :client_id
            AND client_type IN ('Admin', 'Owner')
            """
        ).bindparams(client_id=admin_client_id)
        result = conn.execute(sql)
        admin_groups = [row[0] for row in result]
        if not admin_groups:
            return tuple()
        # Then get the new client's pending groups.
        sql = text(
            """
            SELECT g.obj_uuid, g.label
            FROM obj_m_group_pending p
            JOIN obj_m_group g
            ON p.group_uuid = g.obj_uuid
            WHERE p.member_uuid = :client_id
            AND p.is_active = 1
            AND p.is_approved = 0
            GROUP BY g.obj_uuid
            """
        ).bindparams(client_id=pending_client_id)
        result = conn.execute(sql)
        pending_groups = [{"uuid": row.obj_uuid, "label": row.label} for row in result]
    # Approve the intersection of these groups.
    groups_to_approve = [g for g in pending_groups if g["uuid"] in admin_groups]
    if groups_to_approve:
        with engines["sitemanager"].begin() as conn:
            for group in groups_to_approve:
                sql = text(
                    """
                    INSERT INTO obj_m_mglink
                        (obj_uuid, groupid, memberid, label, client_type, created, modified)
                    VALUES
                        (:uuid, :group_id, :client_id, :label, :client_type, NOW(), NOW())
                    """
                ).bindparams(
                    uuid=str(uuid4()).upper(),
                    group_id=group["uuid"],
                    client_id=pending_client_id,
                    label=f"{pending_client_id}-{group['uuid']}",
                    client_type="Normal",
                )
                conn.execute(sql)
            sql = text(
                """
                UPDATE obj_m_group_pending
                SET is_active = 0, is_approved = 1
                WHERE member_uuid = :client_id
                AND group_uuid IN :groups
                AND is_active = 1
                """
            ).bindparams(
                client_id=pending_client_id,
                groups=tuple(g["uuid"] for g in groups_to_approve),
            )
            conn.execute(sql)
        # Publish client approved event (this is hard-coded for now).
        async with httpx.AsyncClient() as http:
            await http.post(
                f"{domains.stream_proxy}/events/ray:client:approved",
                json={
                    "data": {
                        "client_id": pending_client_id,
                        "username": pending_client_username,
                        "groups": groups_to_approve,
                        "approver": {"client_id": admin_client_id},
                    },
                    "source": "Straker Translate for Slack",
                },
            )
    return tuple(g["uuid"] for g in groups_to_approve)


# log new user info
async def log_new_user_info(user):
    sql = text(
        """
        INSERT IGNORE INTO slack_user_details
            (slack_user_id, slack_team_id, slack_enterprise_id, slack_enterprise_name, timezone, timezone_label, client_email, client_full_name)
        VALUES
            (:user_id, :team_id, :enterprise_id, :enterprise_name, :timezone, :timezone_label, :client_email, :client_full_name)
        """
    ).bindparams(
        user_id=user.get("id", ""),
        team_id=user.get("team_id", ""),
        enterprise_id=user.get("enterprise_user", {}).get("enterprise_id", ""),
        enterprise_name=user.get("enterprise_user", {}).get("enterprise_name", ""),
        timezone=user.get("tz", ""),
        timezone_label=user.get("tz_label", ""),
        client_email=user.get("profile", {}).get("email", ""),
        client_full_name=user.get("profile", {}).get("real_name_normalized", ""),
    )
    await execute(sql, async_engines["ray_integration"], commit_after=True)


async def reactivate_member_for_direct_login(member_id: str) -> None:
    """Re-enable an inactive LC member when they complete Slack Direct Login.

    CBN cleanup may set ``active=0`` on auto-registered Slack users. Direct Login
    recreates the Slack link but ``get_ray_client`` requires ``mem.active = 1``,
    so inactive members must be reactivated here or login appears to fail with
    "Your connected account could not be determined." Soft-deleted members are
    left unchanged.
    """
    sql = text(
        """
        UPDATE obj_m_member
        SET active = 1, email_active = 1, modified = now()
        WHERE obj_uuid = :obj_uuid
        AND is_deleted = 0
        AND (active = 0 OR active IS NULL OR email_active = 0 OR email_active IS NULL)
        """
    ).bindparams(obj_uuid=member_id)
    await execute(sql, async_engines["sitemanager"], commit_after=True)


async def connect_ray_account_sso(
    user_id: str,
    team_id: str,
    email_id: str,
    first_name: str,
    last_name: str,
    channel_id: str,
    enterprise_id: str | None,
) -> str:
    """Connect the LanguageCloud account of a slack user via SSO.

    Args:
        user_id (str): The Slack user ID.
        team_id (str): The Slack team ID.
        email_id (str): The User email ID.
        first_name (str): The User first name ID.
        last_name (str): The User last name ID.
        channel_id (str): The Slack channel ID.
        enterprise_id (str): The Slack enterprise ID.

    Returns:
        bool: The Slack user had a connected LanguageCloud account.
    """
    slack_data = {
        "user_id": user_id,
        "team_id": team_id,
        "email_id": email_id,
        "first_name": first_name,
        "last_name": last_name,
        "channel_id": channel_id,
        "enterprise_id": enterprise_id if enterprise_id is not None else "",
    }
    with engines["sitemanager"].connect() as conn:
        sql = text(
            """
            SELECT obj_uuid, email_primary, login FROM obj_m_member WHERE login = :login
            """
        ).bindparams(login=email_id)
        conn.execute(sql)
        result1 = conn.execute(sql)
    if result1.rowcount == 0:
        # RAY-81247: Slack no longer mints CRM People via Direct Login.
        # Active CRM members can still link; IBM HT without a member uses the
        # service account (Requester/Surrogate stamps).
        raise LookupError(
            "No LanguageCloud account found for this email. "
            "Contact your administrator to be added, or submit Human Translation "
            "without signing in."
        )
    else:
        result = result1.first()
        if not result:
            raise Exception("Member ID not found")
        member_id = result.obj_uuid

        await reactivate_member_for_direct_login(member_id)
        await create_client_access_tokens(client_id=member_id, type="public")
        await create_slack_deltaray_link_sso(
            user_data=json.dumps(slack_data), member_id=member_id
        )
        await add_client_to_slack_group(
            user_data=slack_data,
            member_id=member_id,
        )
        await add_to_verify_team(
            user_uuid=member_id,
            enterprise_id=enterprise_id,
        )
        return member_id


def get_direct_login_group(enterprise_id: str):
    # direct login ibm slack group to insert user
    group_id = "07DA6A86-D635-4383-9598-724D368EF1C3"
    if enterprise_id == "E04RDMG8XP1":
        # on live we treat dev test as ibm group. So when connecting from our enterprise we will add to this group.
        group_id = "173231FA-D524-42BF-9AF3F4834CAA88A0"
    if enterprise_id == "E08AHA89Y1L":
        group_id = "3fcc9bc6-dd12-4bbe-87ac-0633b1585482"
    # for uat ibm slack group uuid is different
    if (
        config.environment != Environment.production
        and config.environment != Environment.local
    ):
        group_id = "C9E4513A-41BC-419A-BEB9-6EDAFCD04470"
        if enterprise_id == "E08AHA89Y1L":
            group_id = "286e0877-ac0a-4252-a1e8-df02cb92b9a8"
    return group_id


def get_direct_login_verify_team(enterprise_id: str | None):
    # direct login ibm slack group to insert user
    team_uuid = "27bcf110-e136-48e8-b3c8-0af7eb83da03"
    if enterprise_id == "E04RDMG8XP1":
        # on live we treat dev test as ibm team. So when connecting from our enterprise we will add to this team.
        team_uuid = "120a1ab0-0b89-4175-b205-aec60ce98de7"
    if enterprise_id == "E08AHA89Y1L":
        team_uuid = "9f5b7edc-47e8-428e-9b38-9164c1445325"
    # for uat ibm slack team id is different
    if (
        config.environment != Environment.production
        and config.environment != Environment.local
    ):
        team_uuid = "818832c3-11fb-41bf-ab30-d97739a684c1"
        if enterprise_id == "E08AHA89Y1L":
            team_uuid = "3b03ca7d-32d9-4bd9-b0e9-5d4dc5dced62"
    return team_uuid


async def add_client_to_slack_group(user_data: dict, member_id: str):
    # function to add user to ibm slack group when they are not in the group
    group_id = get_direct_login_group(user_data.get("enterprise_id", ""))

    sql = text(
        """
        SELECT obj_uuid FROM obj_m_mglink WHERE groupid = :group_id and memberid = :member_id
        """
    ).bindparams(group_id=group_id, member_id=member_id)
    users_in_group = await fetch_one(sql, async_engines["sitemanager"])
    if not users_in_group:
        sqlMgLink = text(
            """
            INSERT INTO obj_m_mglink
                (obj_uuid, groupid, memberid, label, client_type, created, modified)
            VALUES
                (:obj_uuid, :groupid, :memberid, :label, :client_type, now(), now())
            """
        ).bindparams(
            obj_uuid=str(uuid4()).upper(),
            groupid=group_id,
            memberid=member_id,
            label=f"{member_id}-{group_id}",
            client_type="Normal",
        )
        await execute(sqlMgLink, async_engines["sitemanager"], commit_after=True)
    sql = text(
        """
            UPDATE obj_m_member SET groupid = :groupid WHERE obj_uuid = :uuid AND (groupid IS NULL or groupid = '')
            """
    ).bindparams(uuid=member_id, groupid=group_id)
    await execute(sql, async_engines["sitemanager"], commit_after=True)


async def create_client_access_tokens(client_id: str, type: str = "public"):
    """Create API access tokens of a RAY client."""
    # Check if an access token for the account_id already exists
    sql_check = text(
        """
            SELECT 1 FROM access_token
            WHERE account_id = :account_id
        """
    ).bindparams(account_id=client_id)
    result = await fetch_one(sql_check, async_engines["api"])

    # If an access token for the account_id does not exist, insert a new one
    if result is None:
        sql_insert = text(
            """
                INSERT INTO access_token
                    (obj_uuid, account_id, active, `type`, application_id, created_at)
                VALUES
                    (:obj_uuid, :account_id, 1, :type, '', now())
            """
        ).bindparams(obj_uuid=str(uuid4()).upper(), account_id=client_id, type=type)
        await execute(sql_insert, async_engines["api"], commit_after=True)
    # Enable API Access
    sqlMemUpdate = text(
        """
            UPDATE obj_m_member
            SET api_access = 1
            WHERE obj_uuid = :obj_uuid
        """
    ).bindparams(obj_uuid=client_id)
    await execute(sqlMemUpdate, async_engines["sitemanager"], commit_after=True)


async def create_slack_deltaray_link_sso(user_data: str, member_id: str):
    json_data = json.loads(user_data)
    sqlSlackDelete = text(
        """
        DELETE FROM slack_deltaray_link
        WHERE member_uuid = :member_uuid
        """
    ).bindparams(member_uuid=member_id)
    await execute(sqlSlackDelete, async_engines["ray_integration"], commit_after=True)

    sqlSlackAccount = text(
        """
        SELECT slack_user_id
        FROM slack_deltaray_link
        WHERE slack_user_id = :slack_user_id
        AND slack_team_id = :slack_team_id
        """
    ).bindparams(
        slack_user_id=json_data.get("user_id"),
        slack_team_id=json_data.get("team_id"),
    )
    if json_data.get("enterprise_id") is not None:
        sqlSlackAccount = text(
            """
            SELECT slack_user_id
            FROM slack_deltaray_link
            WHERE slack_user_id = :slack_user_id
            AND (slack_enterprise_id = :slack_enterprise_id OR slack_team_id = :slack_team_id)
            """
        ).bindparams(
            slack_user_id=json_data.get("user_id"),
            slack_team_id=json_data.get("team_id"),
            slack_enterprise_id=json_data.get("enterprise_id"),
        )
    resultSlackAccount = await fetch_one(
        sqlSlackAccount, async_engines["ray_integration"]
    )

    if resultSlackAccount is not None:
        if json_data.get("enterprise_id") is not None:
            sqlRayUpdate = text(
                """
                UPDATE slack_deltaray_link SET
                    member_uuid = :member_uuid,
                    slack_user_id = :user_id,
                    slack_team_id = :team_id,
                    slack_enterprise_id = :enterprise_id,
                    slack_channel_id = :channel_id,
                    is_subscribed = 1,
                    is_active = 1,
                    is_sso = 1,
                    activated_at = now()
                WHERE (slack_user_id = :user_id)
                AND (
                    slack_team_id = :team_id
                    OR slack_enterprise_id = :enterprise_id
                )
                """
            ).bindparams(
                member_uuid=member_id,
                user_id=json_data.get("user_id"),
                team_id=json_data.get("team_id"),
                enterprise_id=json_data.get("enterprise_id"),
                channel_id=json_data.get("channel_id"),
            )
        else:
            sqlRayUpdate = text(
                """
                UPDATE slack_deltaray_link SET
                    member_uuid = :member_uuid,
                    slack_user_id = :user_id,
                    slack_team_id = :team_id,
                    slack_enterprise_id = :enterprise_id,
                    slack_channel_id = :channel_id,
                    is_subscribed = 1,
                    is_active = 1,
                    is_sso = 1,
                    activated_at = now()
                WHERE slack_user_id = :user_id
                AND slack_team_id = :team_id
                """
            ).bindparams(
                member_uuid=member_id,
                user_id=json_data.get("user_id"),
                team_id=json_data.get("team_id"),
                enterprise_id=json_data.get("enterprise_id"),
                channel_id=json_data.get("channel_id"),
            )
        await execute(sqlRayUpdate, async_engines["ray_integration"], commit_after=True)
    else:
        sqlRay = text(
            """
            INSERT INTO slack_deltaray_link
                (slack_user_id, slack_team_id, slack_enterprise_id, slack_channel_id, member_uuid, is_subscribed, is_active, is_sso, activated_at)
            VALUES
                (:user_id, :team_id, :enterprise_id, :channel_id, :member_uuid, 1, 1, 1, now())
            """
        ).bindparams(
            user_id=json_data.get("user_id"),
            team_id=json_data.get("team_id"),
            enterprise_id=json_data.get("enterprise_id"),
            channel_id=json_data.get("channel_id"),
            member_uuid=member_id,
        )
        await execute(sqlRay, async_engines["ray_integration"], commit_after=True)


async def crete_slack_logs_sso(user_data: str, member_id: str, message: str):
    json_data = json.loads(user_data)
    sql = text(
        """
        INSERT INTO slack_logs_sso
            (user_id, team_id, channel_id, client_uuid, payload, message)
        VALUES
            (:user_id, :team_id, :channel_id, :client_uuid, :payload, :message)
        """
    ).bindparams(
        user_id=json_data.get("user_id"),
        team_id=json_data.get("team_id"),
        channel_id=json_data.get("channel_id"),
        client_uuid=member_id,
        payload=user_data,
        message=message,
    )
    await execute(sql, async_engines["ray_integration_log"], commit_after=True)


async def get_client_tokens(languagecloud_api_key: str):
    """http languagecloud API to get the client tokens."""
    url = f"{domains.languagecloud_api}/credits/balance"
    headers = {
        "Authorization": f"Bearer {languagecloud_api_key}",
    }
    max_retries = 3
    base_delay = 1.0  # Start with 1 second delay

    for attempt in range(max_retries + 1):
        try:
            # Initial timeout is 5 seconds, increase to 30 seconds on retries
            timeout = 5.0 if attempt == 0 else 30.0
            async with httpx.AsyncClient(timeout=timeout) as http:
                response = await http.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
                return GetCreditBalanceResponse(
                    ai_token=data["ai_token"], mt_token=data["mt_token"]
                )
        except httpx.ReadTimeout as e:
            # Only retry on read timeout errors
            if attempt < max_retries:
                # Calculate exponential backoff: base_delay * (2 ^ attempt)
                delay = base_delay * (2**attempt)
                await asyncio.sleep(delay)
                continue
            # Last attempt failed, log and return default
            notify_exception(e)
            return GetCreditBalanceResponse(0, 0)
        except Exception as e:
            # For all other exceptions, log and return default immediately
            notify_exception(e)
            return GetCreditBalanceResponse(0, 0)


async def get_verify_trial_status(
    id_token: str | None,
) -> tuple[bool | None, int | None]:
    """Fetch the effective Verify session trial status for the current user."""
    if not id_token:
        return None, None

    url = f"{domains.languagecloud_api}/session"
    headers = {"Authorization": f"Bearer {id_token}"}
    params = {"app": "verify"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            response = await http.get(url, headers=headers, params=params)
            response.raise_for_status()
    except httpx.HTTPError as e:
        notify_exception(e, "Failed to fetch Verify session trial status")
        return None, None

    data = response.json()
    return data.get("is_trial"), data.get("trial_remaining")


async def get_group_tokens(org_uuid: str):
    """read sitemanager.obj_m_member_credit_transactions to get the group tokens balance."""

    sql = text(
        """
        SELECT SUM(amount) AS total
        FROM obj_m_member_credit_transactions
        WHERE organization_uuid = :org_uuid
        AND credit_type = 'ai_token'
        """
    ).bindparams(bindparam("org_uuid", value=org_uuid))
    result = await fetch_one(sql, async_engines["sitemanager_readonly"])
    if not result or not result["total"]:
        return GetCreditBalanceResponse(0, 0)
    return GetCreditBalanceResponse(ai_token=result["total"], mt_token=0)


async def log_transcribe_request(
    duration_ms: int,
    file_name: str,
    ray_connection: RayConnection,
) -> int:
    """
    Call languagecloud api to log the transcription request and consumer tokens
    """

    tokens = duration_to_tokens(duration_ms)
    if not tokens:
        raise Exception("Duration is 0")
    if not ray_connection.client:
        raise Exception("No client connection")
    url = f"{domains.languagecloud_api}/mt/transcribe"
    headers = {
        "Authorization": f"Bearer {ray_connection.client.id_token}",
    }
    data = {
        "duration_ms": duration_ms,
        "app_name": "slack",
        "file_name": file_name,
    }
    async with httpx.AsyncClient() as http:
        response = await http.post(url, headers=headers, json=data)
        response.raise_for_status()
        return tokens


def duration_to_tokens(duration_ms: int) -> int:
    """
    Convert duration to tokens.
    """
    cost_per_min = 2  # $2
    token_value = 0.02  # $0.002
    duration_per_token_min = token_value / cost_per_min  # min
    duration_per_token_ms = duration_per_token_min * 60 * 1000
    # 600 ms per token
    return math.ceil(duration_ms / duration_per_token_ms)


def duration_to_subtitling_tokens(duration_ms: int) -> int:
    """
    Convert duration to tokens for subtitling feature.

    Cost model:
    - cost_per_min = $0.60 (60 cents per minute)
    - token_value = $0.02 (2 cents per token)
    - tokens_per_min = cost_per_min / token_value = 0.60 / 0.02 = 30 tokens per minute

    Args:
        duration_ms: Duration in milliseconds

    Returns:
        Number of tokens (ceiled)
    """
    token_value = 0.02  # $0.02
    cost_per_min = 0.60  # $0.60
    tokens_per_min = cost_per_min / token_value  # 30 tokens per minute
    duration_minutes = duration_ms / 60000  # Convert ms to minutes
    return math.ceil(duration_minutes * tokens_per_min)


def build_spend_idempotency_key(
    app_source: str,
    submission_id: str,
    service: str,
    target_language: str = "",
    unit_type: str = "",
) -> str:
    """Build a stable idempotency key for a credit spend (RAY-80000).

    Derived from a producer-owned, per-billable-intent id (``submission_id`` —
    the transcription task uuid) so a redelivered/retried task replays to one
    debit, while two genuinely distinct tasks produce different keys and are
    charged separately.
    """
    raw = f"{app_source}:{submission_id}:{service}:{target_language}:{unit_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _id_token_for_client(
    client_id: str, *, allow_group_fallback: bool = False
) -> str:
    """Mint a LanguageCloud id-token for ``client_id`` (RAY-80000).

    Looks up the ``obj_m_member`` row and signs a user id-token. When the poster
    has no member row (channel auto-translate bills the org), ``allow_group_fallback``
    signs a group token for the org uuid instead of raising.
    """
    sql = text(
        """
        SELECT m.obj_uuid, m.given_name, m.family_name, m.email_primary, m.active
        FROM obj_m_member m
        WHERE m.obj_uuid = :client_id
        """
    ).bindparams(client_id=client_id)
    result = await fetch_one(sql, async_engines["sitemanager_readonly"])
    if result:
        return create_languagecloud_id_token(
            uuid=client_id,
            given_name=result["given_name"] or "",
            family_name=result["family_name"] or "",
            email=result["email_primary"] or "",
            is_active=bool(result["active"]),
            aud="languagecloud-api",
            secret=config.languagecloud_api_key.get_secret_value(),
        )
    if allow_group_fallback:
        return create_languagecloud_group_token(
            uuid=client_id,
            aud="languagecloud-api",
            secret=config.languagecloud_api_key.get_secret_value(),
        )
    raise Exception(f"Client {client_id} not found")


async def log_transcribe_by_client_id(
    client_id: str,
    duration_ms: int,
    file_name: str,
    source_language: str | None = None,
    idempotency_key: str | None = None,
    submission_group_uuid: str | None = None,
    group_uuid: str | None = None,
    email: str | None = None,
    client_name: str | None = None,
) -> tuple[int, str]:
    """
    Charge a transcription via the LanguageCloud API (``/mt/transcribe``) using
    client_id. The gateway writes the debit *and* its self-describing
    ``credit_transaction_usage`` row in one transaction (RAY-80000), so this is
    the canonical charging path for transcription — preferred over a direct
    credit-ledger write, which would leave the usage row missing.

    Args:
        client_id: The LanguageCloud client UUID
        duration_ms: Duration of the media in milliseconds
        file_name: Name of the transcribed file
        source_language: Whisper-detected source language, persisted on the row
        idempotency_key: Stable per-task key so a replay is charged once
        group_uuid: CRM billing group for org-billed media (RAY-81247)
        email: Slack poster email for usage-report Client Email (RAY-81247)
        client_name: Slack poster display name (RAY-81247)

    Returns:
        tuple[int, str]: (tokens consumed, gateway transaction UUID)
    """
    tokens = duration_to_tokens(duration_ms)
    if not tokens:
        raise Exception("Duration is 0")

    id_token = await _id_token_for_client(client_id, allow_group_fallback=True)

    url = f"{domains.languagecloud_api}/mt/transcribe"
    headers = {
        "Authorization": f"Bearer {id_token}",
    }
    data: dict[str, Any] = {
        "duration_ms": duration_ms,
        "app_name": "slack",
        "file_name": file_name,
    }
    if source_language:
        data["source_language"] = source_language
    if idempotency_key:
        data["idempotency_key"] = idempotency_key
    # Media submission id so transcribe/translate/embed share a group (RAY-80417).
    if submission_group_uuid:
        data["submission_group_uuid"] = submission_group_uuid
    if group_uuid:
        data["group_uuid"] = group_uuid
    if email:
        data["email"] = email
    if client_name:
        data["client_name"] = client_name
    async with httpx.AsyncClient() as http:
        response = await http.post(url, headers=headers, json=data)
        response.raise_for_status()
        body = response.json() or {}
        return tokens, body.get("transaction_uuid", "")


async def log_document_mt_by_client_id(
    client_id: str,
    charge: dict[str, Any],
) -> dict[str, Any]:
    """Charge document MT (+ optional combined PDF fee) via ``/mt/transaction``.

    Called from ``charge_document_mt`` after the translated file is delivered to
    Slack so both the document-MT and PDF conversion debits are absorbed when
    translation or delivery fails (RAY-80417). ``charge`` is the prepared
    ``DocumentTransaction`` payload built by the consumer and carried on the
    delivery event; this only authenticates as the client and relays it.

    Returns:
        dict: the gateway response (``transaction_uuid``, ``pdf_transaction_uuid``).
    """
    id_token = await _id_token_for_client(client_id, allow_group_fallback=True)

    url = f"{domains.languagecloud_api}/mt/transaction"
    headers = {"Authorization": f"Bearer {id_token}"}
    async with httpx.AsyncClient() as http:
        response = await http.post(url, headers=headers, json=charge)
        response.raise_for_status()
        return response.json() or {}


async def log_inline_mt_usage_by_client_id(
    client_id: str,
    text_length: int,
    target_languages: list[str],
    usage_type: str,
    app_name: str = "slack",
    source_language: str | None = None,
    engine: str | None = None,
    channel_name: str | None = None,
    word_count: int | None = None,
    idempotency_key: str | None = None,
    email: str | None = None,
    client_name: str | None = None,
    is_bot: bool | None = None,
    group_uuid: str | None = None,
    submission_group_uuid: str | None = None,
) -> str:
    """
    Charge inline/channel/shortcut MT via the LanguageCloud API
    (``/mt/inline-usage``) using client_id. The translation itself is performed
    by the sup-mt-service pipeline (glossaries + multi-service routing), so it
    cannot use ``/mt/translate``; this endpoint only records the debit *and* its
    self-describing ``credit_transaction_usage`` row in one transaction with
    idempotency (RAY-80000 §3.4) — replacing a direct credit-ledger write that
    left the usage row missing.

    The gateway charge is ``ceil(text_length * len(target_languages) * 0.002)`` —
    same ×-targets shape as the legacy ``0.1`` formula, SOW rate only (RAY-80492).
    ``target_languages`` still lists one entry per billed (service, language) pair.

    Returns:
        str: the gateway transaction UUID.
    """
    # Channel auto-translate bills the org when the poster has no obj_m_member
    # row (client_id is the org uuid); the group fallback authenticates as the
    # org instead of failing (RAY-80000).
    id_token = await _id_token_for_client(client_id, allow_group_fallback=True)

    url = f"{domains.languagecloud_api}/mt/inline-usage"
    headers = {
        "Authorization": f"Bearer {id_token}",
    }
    data: dict[str, Any] = {
        "text_length": text_length,
        "target_languages": target_languages,
        "app_name": app_name,
        "usage_type": usage_type,
    }
    if source_language:
        data["source_language"] = source_language
    if engine:
        data["engine"] = engine
    if channel_name:
        data["channel_name"] = channel_name
    # word_count is a typed report column (RAY-80000); billing stays
    # character-based, so it is sent only when the producer computed it.
    if word_count is not None:
        data["word_count"] = word_count
    if idempotency_key:
        data["idempotency_key"] = idempotency_key
    # Channel/shortcut MT is billed against the group, so the report cannot
    # resolve the poster from client_uuid; send the Slack user identity so the
    # usage row carries it (RAY-80000).
    if email:
        data["email"] = email
    if client_name:
        data["client_name"] = client_name
    if is_bot is not None:
        data["is_bot"] = is_bot
    # Billing group for the ledger debit. Org-billed channel/shortcut MT
    # authenticates as the org, so the gateway would otherwise record group_uuid as
    # the org; send the resolved billing group to keep group attribution as it was
    # before the gateway migration (RAY-80000 hotfix).
    if group_uuid:
        data["group_uuid"] = group_uuid
    if submission_group_uuid:
        data["submission_group_uuid"] = submission_group_uuid
    async with httpx.AsyncClient() as http:
        response = await http.post(url, headers=headers, json=data)
        response.raise_for_status()
        body = response.json() or {}
        return body.get("transaction_uuid", "")


async def log_embedding_by_client_id(
    client_id: str,
    duration_ms: int,
    num_target_languages: int,
    target_languages: list[str] | None = None,
    source_language: str | None = None,
    file_name: str | None = None,
    app_name: str = "slack",
    idempotency_key: str | None = None,
    submission_group_uuid: str | None = None,
    group_uuid: str | None = None,
    email: str | None = None,
    client_name: str | None = None,
) -> str:
    """
    Charge media subtitle embedding via the LanguageCloud API (``/mt/embed``)
    using client_id. The gateway writes the debit *and* its
    ``credit_transaction_usage`` row in one transaction with idempotency
    (RAY-80000 §3.5), replacing a direct credit-ledger write that left no usage
    row. Target codes are listed in metadata; source_language is the detected
    audio language when supplied.

    Returns:
        str: the gateway transaction UUID.
    """
    id_token = await _id_token_for_client(client_id, allow_group_fallback=True)

    url = f"{domains.languagecloud_api}/mt/embed"
    headers = {
        "Authorization": f"Bearer {id_token}",
    }
    data: dict[str, Any] = {
        "duration_ms": duration_ms,
        "num_target_languages": num_target_languages,
        "app_name": app_name,
    }
    if target_languages:
        data["target_languages"] = target_languages
    if source_language:
        data["source_language"] = source_language
    if file_name:
        data["file_name"] = file_name
    if idempotency_key:
        data["idempotency_key"] = idempotency_key
    # Media submission id so transcribe/translate/embed share a group (RAY-80417).
    if submission_group_uuid:
        data["submission_group_uuid"] = submission_group_uuid
    if group_uuid:
        data["group_uuid"] = group_uuid
    if email:
        data["email"] = email
    if client_name:
        data["client_name"] = client_name
    async with httpx.AsyncClient() as http:
        response = await http.post(url, headers=headers, json=data)
        response.raise_for_status()
        body = response.json() or {}
        return body.get("transaction_uuid", "")


async def get_client_type(client_id: str, group_id: str | None):
    """Get the client type for a group. Owner Admin or Normal client"""
    if not group_id:
        return None
    sql = text(
        """
        SELECT client_type
        FROM obj_m_mglink
        WHERE memberid = :client_id
        AND groupid = :group_id
        AND is_active = 1
        """
    ).bindparams(client_id=client_id, group_id=group_id)
    result = await fetch_one(sql, async_engines["sitemanager_readonly"])
    if not result:
        return None
    return result["client_type"]


async def user_is_organization_group_admin(
    client_id: str | None, organization_id: str | None
) -> bool:
    """True if the member is active Admin/Owner of any CRM group under the org.

    Uses workspace ``verify_organization_uuid`` → child ``obj_m_group`` rows
    (e.g. “IBM Slack App”). Does **not** use ``obj_m_member.groupid`` (default
    group); inactive mglinks do not count.
    """
    if not client_id or not organization_id:
        return False
    sql = text(
        """
        SELECT 1 AS ok
        FROM obj_m_mglink link
        JOIN obj_m_group g ON g.obj_uuid = link.groupid
        WHERE link.memberid = :client_id
          AND link.is_active = 1
          AND link.client_type IN ('Admin', 'Owner')
          AND g.organization_id = :organization_id
        LIMIT 1
        """
    ).bindparams(client_id=client_id, organization_id=organization_id)
    result = await fetch_one(sql, async_engines["sitemanager_readonly"])
    return bool(result)


async def user_may_receive_quotes(ray: RayConnection | None) -> bool:
    """Return whether this connection should see Slack quote Accept UI.

    When ``config.quote_admin_only`` is false, everyone receives quotes.
    Otherwise only Verify group Admin/Owner members do; org-billed posters
    without a member client auto-proceed without quote UX.

    Admin/Owner is accepted on the workspace-linked super group **or** on any
    active LC group under the workspace Verify org (e.g. “IBM Slack App”).
    The member’s default group (``obj_m_member.groupid`` / ``user_group_id``)
    is not used. Without workspace super-group context, quotes are denied.
    """
    from app.config import config

    if not config.quote_admin_only:
        return True
    if ray is None or ray.client is None:
        return False

    client_id = ray.client.id
    super_groups = getattr(ray, "super_group", None) or []
    if not super_groups or not getattr(super_groups[0], "id", None):
        return False

    sg = super_groups[0]
    if await get_client_type(client_id, sg.id) in ("Admin", "Owner"):
        return True
    org_id = getattr(sg, "verify_organization_uuid", None) or None
    return await user_is_organization_group_admin(client_id, org_id)


async def get_job_group_quote_settings(job_id: str):
    """Get the quote settings for the job group."""
    sql = text(
        """
        SELECT api_enabled
        FROM obj_m_group g
        JOIN franchise.obj_tp_job j
        ON g.obj_uuid = j.groupid
        WHERE j.id = :job_id
        """
    ).bindparams(job_id=job_id)
    result = await fetch_one(sql, async_engines["sitemanager_readonly"])
    if not result:
        return False
    return result["api_enabled"]


async def get_group_quote_settings(group_uuid: str):
    """Get the quote settings for the job group."""
    sql = text(
        """
        SELECT api_enabled
        FROM obj_m_group g
        WHERE g.obj_uuid = :group_uuid
        """
    ).bindparams(group_uuid=group_uuid)
    result = await fetch_one(sql, async_engines["sitemanager_readonly"])
    if not result:
        return False
    return result["api_enabled"]


async def get_all_tokens_for_enterprise(
    enterprise_id: str | None,
) -> list[dict[str, Any]] | None:
    """Get all the tokens for the enterprise"""
    if not enterprise_id:
        return None
    sql = text(
        """
        SELECT sb.team_id, sb.bot_token
        FROM slack_bots sb
        JOIN (
            SELECT team_id, MAX(id) as max_id
            FROM slack_bots
            WHERE enterprise_id = :enterprise_id
            GROUP BY team_id
        ) latest_bots ON sb.id = latest_bots.max_id
        WHERE sb.bot_token IS NOT NULL AND sb.bot_token <> ''
        ORDER BY sb.id DESC
        """
    ).bindparams(enterprise_id=enterprise_id)
    result = await fetch_all(sql, async_engines["ray_integration"])
    if not result:
        return None
    return result


async def get_team_from_token(token: str | None):
    """Get the team ID from the bot token"""
    if not token:
        return None
    sql = text(
        """
        SELECT team_id
        FROM slack_bots
        WHERE bot_token = :bot_token
        """
    ).bindparams(bot_token=token)
    result = await fetch_one(sql, async_engines["ray_integration"])
    if not result:
        return None
    return result["team_id"]


async def get_channel_info(channel_id: str, client: AsyncWebClient, team_id: str):
    """Get the quote settings for the job group."""
    channel_info = await client.conversations_info(channel=channel_id)
    return {
        "team_id": team_id,
        "channel_id": channel_id,
        "bot_token": client.token,
        "name": channel_info["channel"]["name"],
        "is_private": channel_info["channel"]["is_private"],
    }


async def resolve_channels_to_team(
    channel_id: str, client: AsyncWebClient, enterprise_id: str | None, team_id: str
) -> dict[str, bool | str | None]:
    """Resolve a channel ID to a team ID. Use conversation info API to get the team ID.
        When error attempt to get all tokens for the enterprise with each token
    Args:
        channel_id (str): The Slack channel ID.

    Returns:
        str: The Slack team ID.
    """
    team_channel = {
        "team_id": team_id,
        "channel_id": channel_id,
        "bot_token": client.token,
        "name": "unknown",
        "is_private": False,
    }
    old_token = client.token
    if enterprise_id:
        all_tokens = await get_all_tokens_for_enterprise(enterprise_id)
    try:
        channel_info = await client.conversations_info(channel=channel_id)
        team_channel = {
            "team_id": channel_info["channel"]["context_team_id"],
            "channel_id": channel_id,
            "bot_token": client.token,
            "name": channel_info["channel"]["name"],
            "is_private": channel_info["channel"]["is_private"],
        }
    except SlackApiError as e:
        successful = False
        if all_tokens:
            for token in all_tokens:
                client.token = token["bot_token"]
                try:
                    channel_info = await client.conversations_info(channel=channel_id)
                    team_channel = {
                        "team_id": token["team_id"],
                        "channel_id": channel_id,
                        "bot_token": token["bot_token"],
                        "name": channel_info["channel"]["name"],
                        "is_private": channel_info["channel"]["is_private"],
                    }
                    successful = True
                    break  # Exit the loop if a successful token is found
                except SlackApiError:
                    continue
        if not successful:
            client.token = old_token
            raise e  # Raise the original SlackApiError if no token was successful. This will request that the app be added to the workspace/channel.
    client.token = old_token

    return team_channel


async def is_slack_team_admin(client_uuid: str, enterprise_id: str | None):
    if not enterprise_id:
        return False
    if enterprise_id == "E04RDMG8XP1":
        return True
    group_id = get_direct_login_group(enterprise_id)
    client_type = await get_client_type(client_uuid, group_id)
    return client_type in ["Admin", "Owner"]


async def get_group_mt_engine(
    group_uuid: str,
    is_group: bool = False,
) -> str:
    """Gets user super group or group MT engine settings.

    Args:
        group_uuid (str): user assign group uuid
        mt_engine (str): MT engine name. Defaults to "google".
    """

    ai_inherit = "ai_mt"
    group_id = group_uuid
    mt_engine = "google"
    if not is_group:
        # CHECK IF IS INHERIT FROM SUPER GROUP
        sql = text(
            """
                SELECT super_group_uuid
                FROM super_group_glink
                WHERE group_uuid = :group_uuid
                AND property_to_inherit = :ai_inherit
            """
        ).bindparams(group_uuid=group_uuid, ai_inherit=ai_inherit)
        rows = await fetch_all(sql, async_engines["sitemanager"])
        if rows:
            group_id = rows[0]["super_group_uuid"]

    # GET GROUP MT ENGINE
    sql = text(
        """
        SELECT ai_mt
        FROM obj_m_group
        WHERE obj_uuid = :group_id
        """
    ).bindparams(group_id=group_id)
    row = await fetch_one(sql, async_engines["sitemanager_readonly"])
    if row is not None and row["ai_mt"] is not None and row["ai_mt"] != "":
        mt_engine = row["ai_mt"]

    return mt_engine


async def is_verify_job(job_uuid: str):
    """Check if the job is a verify job."""
    sql = text(
        """
        SELECT jobtype
        FROM franchise.obj_tp_job
        WHERE obj_uuid = :job_uuid
        """
    ).bindparams(job_uuid=job_uuid)
    result = await fetch_one(sql, async_engines["sitemanager_readonly"])
    if not result:
        return False
    return result["jobtype"] == "Verify"


async def get_token_for_team(team_id: str):
    """Get the bot token for a team."""
    sql = text(
        """
        SELECT bot_token
        FROM slack_bots
        WHERE team_id = :team_id
        ORDER BY id DESC
        LIMIT 1
        """
    ).bindparams(team_id=team_id)
    result = await fetch_one(sql, async_engines["ray_integration"])
    if not result:
        return None
    return result["bot_token"]


async def add_to_verify_team(user_uuid: str, enterprise_id: str | None):
    """Add user to verify team"""
    team_uuid = get_direct_login_verify_team(enterprise_id)
    # check if user is already in the team
    sql = text(
        """
            SELECT team_uuid
            FROM verify_team_user_link
            WHERE user_uuid = :user_uuid
            AND team_uuid = :team_uuid
        """
    ).bindparams(user_uuid=user_uuid, team_uuid=team_uuid)
    result = await fetch_one(sql, async_engines["sitemanager"])
    if not result:
        # delete from existing team
        sql = text(
            """
                DELETE from verify_team_user_link where user_uuid = :user_uuid
            """
        ).bindparams(user_uuid=user_uuid)
        await execute(sql, async_engines["sitemanager"], commit_after=True)
        # delete from user_roles
        sql = text(
            """
                DELETE from user_roles where user_id = :user_id
            """
        ).bindparams(user_id=user_uuid)
        await execute(sql, async_engines["sitemanager"], commit_after=True)
        # add to verify team
        sql = text(
            """
            INSERT INTO verify_team_user_link
                (user_uuid, team_uuid, user_role)
            VALUES
                (:user_uuid, :team_uuid, 'member')
            """
        ).bindparams(user_uuid=user_uuid, team_uuid=team_uuid)
        await execute(sql, async_engines["sitemanager"], commit_after=True)
        # add to user_roles
        sql = text(
            """
            INSERT INTO user_roles
                (user_id, team_id, role_id)
            VALUES
                (:user_uuid, :team_uuid, '83d64046-770b-43f5-abbf-e96ca0b3db9a')
            """
        ).bindparams(user_uuid=user_uuid, team_uuid=team_uuid)
        await execute(sql, async_engines["sitemanager"], commit_after=True)
    # check if user has role if not add role
    else:
        sql = text(
            """
            SELECT role_id
            FROM user_roles
            WHERE user_id = :user_uuid
            AND team_id = :team_uuid
            """
        ).bindparams(user_uuid=user_uuid, team_uuid=team_uuid)
        result = await fetch_one(sql, async_engines["sitemanager"])
        if not result:
            sql = text(
                """
                INSERT INTO user_roles
                    (user_id, team_id, role_id)
                VALUES
                    (:user_uuid, :team_uuid, '83d64046-770b-43f5-abbf-e96ca0b3db9a')
                """
            ).bindparams(user_uuid=user_uuid, team_uuid=team_uuid)
            await execute(sql, async_engines["sitemanager"], commit_after=True)
