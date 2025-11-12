"""This module contains functions to connect this app to to
other services, e.g. Slack, RAY apps.
"""

import asyncio
import hashlib
import json
import math
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from buglog import notify_exception
from ray_logger.slack import SlackAppLog
from slack_bolt.context.async_context import AsyncBoltContext
from slack_sdk.errors import SlackApiError
from slack_sdk.oauth.installation_store import Installation
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import bindparam, text
from straker_auth.languagecloud import create_languagecloud_id_token
from straker_utils.sql.async_engine import execute, fetch_all, fetch_one

from ..config import Environment, config, domains
from ..database import async_engines, engines
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


def is_ibm_super_group(
    enterprise_id: str | None = None,
) -> bool:
    """Gets the LanguageCloud super group linked to the Slack workspace if an active
    link exists, otherwise returns None.

    Args:
        team_id (str): The ID of the team.
    """
    if not enterprise_id:
        return False

    with engines["ray_integration_readonly"].connect() as conn:
        sql = text(
            """
            SELECT link.super_group_uuid, g.label
            FROM slack_super_group_link link
            INNER JOIN sitemanager.obj_m_group g
            ON link.super_group_uuid = g.obj_uuid
            WHERE link.slack_enterprise_id = :enterprise_id
            AND link.is_active = 1
            AND (
                link.super_group_uuid = '9ADE9F44-92A4-4EEE-9BCC-96AFEF9B6D36'
                OR link.super_group_uuid = '13D8D894-3DC5-49DC-9DD0-AD9EA537E597'
                OR link.super_group_uuid = '94c8dd41-9029-4aae-883a-57e4b86ead17'
                OR link.super_group_uuid = '7f8bcd96-3856-43d6-a01e-3d4c4c196558'
            ) AND link.verify_organization_uuid is not null
            """
        ).bindparams(enterprise_id=enterprise_id)
        result = conn.execute(sql)
        rows = result.fetchall()
        if not rows:
            return False
    return True


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
        planname="Enterprise",
        sso=False,
    )


async def get_ray_client(
    user_id: str, team_id: str, enterprise_id: str | None = None
) -> RayClient | None:
    """Gets the LanguageCloud client id and username linked to the Slack account if an active
    link exists, otherwise returns None.

    Args:
        user_id (str): The Slack user ID.
        team_id (str): The Slack team ID.
        enterprise_id (str | None): The Slack enterprise ID.
    """
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
        groupid,
        is_sso,
        slack_access_token,
        settings_id,
    ) = (
        row["member_uuid"],
        row["groupid"],
        row["login"],
        row["groupid"],
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

    # TODO Fix this, sometimes the plan is incorrect.
    # get group subscription plan
    sql = text(
        """
                SELECT psp.plan_name
                FROM ps_service ps
                LEFT JOIN ps_plan psp
                ON psp.service_type_uuid = ps.service_type_uuid
                LEFT JOIN ps_subscription pss
                ON pss.ps_service_uuid = ps.obj_uuid
                LEFT JOIN ps_subscription_billing psb
                ON psb.ps_subscription_uuid = pss.obj_uuid
                WHERE ps.group_uuid = :group_uuid
                AND pss.is_active = 1
                AND psb.expiry > NOW()
                """
    ).bindparams(group_uuid=groupid)
    plan_rows = await fetch_all(sql, async_engines["sitemanager_readonly"])
    if not plan_rows:
        plan = "Free"
    else:
        plan = plan_rows[0]["plan_name"]
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
        planname=plan,
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
    # When ibm enterprise and user is not admin, disable verify in slack
    if (
        client
        and super_group[0].enable_verify_in_slack
        and is_ibm_super_group(enterprise_id)
        and not await is_slack_team_admin(client.id, enterprise_id)
    ):
        super_group = [
            RaySuperGroup(
                id=group.id,
                name=group.name,
                slack_team_id=group.slack_team_id,
                slack_enterprise_id=group.slack_enterprise_id,
                enable_verify_in_slack=False,
                verify_organization_uuid=group.verify_organization_uuid,
            )
            for group in super_group
        ]

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
        member_id = str(uuid4()).upper()
        # Create User and User Group Link
        await create_client_and_mglink(
            user_data=json.dumps(slack_data),
            member_id=member_id,
        )
        # Create log
        # crete_slack_logs_sso(user_data=json.dumps(slack_data), member_id=member_id, message="New User")
        # Create User Access Token
        await create_client_access_tokens(client_id=member_id, type="public")
        # Create User Slack Link
        await create_slack_deltaray_link_sso(
            user_data=json.dumps(slack_data), member_id=member_id
        )
        await add_to_verify_team(
            user_uuid=member_id,
            enterprise_id=enterprise_id,
        )
        return member_id
    else:
        result = result1.first()
        if not result:
            raise Exception("Member ID not found")
        member_id = result.obj_uuid

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


async def create_client_and_mglink(
    user_data: str,
    member_id: str,
):
    json_data = json.loads(user_data)
    group_id = get_direct_login_group(json_data.get("enterprise_id"))
    password = "secret".encode("utf-8")  # Convert the password to bytes
    hash_object = hashlib.sha512(password)
    with engines["sitemanager"].connect() as conn:
        # Get Account Manager
        sqlAm = text(
            """
                SELECT account_manager
                FROM obj_m_group
                WHERE obj_uuid = :obj_uuid
            """
        ).bindparams(obj_uuid=group_id)
        conn.execute(sqlAm)
        resultAm = conn.execute(sqlAm).first()
        account_manager = resultAm.account_manager if resultAm is not None else None
        # Create User
        sqlMem = text(
            """
            INSERT INTO obj_m_member
                (obj_uuid, email_primary, login, password, password_updated, given_name, family_name, created, modified, account_manager, groupid, product, subscribed, active, email_active)
            VALUES
                (:obj_uuid, :email_primary, :email_primary, :password, now(), :given_name, :family_name, now(), now(), :account_manager, :groupid, :product, 1, 1, 1)
            """
        ).bindparams(
            obj_uuid=member_id,
            email_primary=json_data.get("email_id"),
            given_name=json_data.get("first_name"),
            family_name=json_data.get("last_name"),
            password=hash_object.hexdigest().upper(),
            account_manager=account_manager,
            groupid=group_id,
            product="Slack",
        )
        conn.execute(sqlMem)
        conn.commit()
        # Create User Group Link
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
        conn.execute(sqlMgLink)
        conn.commit()


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
        AND is_active = 0
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
            AND is_active = 0
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
    # 60 ms per token
    return math.ceil(duration_ms / duration_per_token_ms)


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
        """
    ).bindparams(client_id=client_id, group_id=group_id)
    result = await fetch_one(sql, async_engines["sitemanager_readonly"])
    if not result:
        return None
    return result["client_type"]


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
