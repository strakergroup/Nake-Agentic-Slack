"""IBM Slack HT reporting-only identity (RAY-81247).

IBM Slack HT submissions own the Verify/franchise job as a fixed CRM service
member (CAITS-style). The Slack poster is stamped only on group custom fields
Requester ID / Surrogate ID for SO + usage reporting — not as TJ clientid.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from straker_auth.languagecloud import create_languagecloud_id_token
from straker_utils.sql.async_engine import fetch_one

from app.auth.connector import RayClient, RayConnection
from app.config import config
from app.database import async_engines
from app.ray.utils import is_ibm_enterprise

logger = logging.getLogger(__name__)

# Prod IBM Slack App Admin (Chris Sacre) — override via HT_SERVICE_ACCOUNT_MEMBER_UUID.
DEFAULT_HT_SERVICE_ACCOUNT_MEMBER_UUID = "D8CF434C-6FAD-496F-9A6B-C8EE6A0A066B"

REQUESTER_ID_LABEL = "Requester ID"
SURROGATE_ID_LABEL = "Surrogate ID"


def ht_service_account_member_uuid() -> str:
    return (
        config.ht_service_account_member_uuid or ""
    ).strip() or DEFAULT_HT_SERVICE_ACCOUNT_MEMBER_UUID


def should_use_ht_service_account(
    enterprise_id: str | None,
    ray: RayConnection | None,
) -> bool:
    """True when IBM workspace is linked and the Slack poster has no active CRM member.

    Active CRM members keep owning their own HT jobs. Service account is only for
    reporters-only posters (no ``obj_m_member`` / Slack deltaray link).
    """
    if not is_ibm_enterprise(enterprise_id):
        return False
    if ray is None or not ray.super_group:
        return False
    return ray.client is None


def requester_surrogate_custom_fields(poster_email: str) -> list[dict[str, str]]:
    """Build Requester/Surrogate stamps. Empty when email cannot be resolved."""
    email = (poster_email or "").strip()
    if not email:
        return []
    return [
        {"label": REQUESTER_ID_LABEL, "value": email},
        {"label": SURROGATE_ID_LABEL, "value": email},
    ]


def custom_fields_form_value(poster_email: str) -> str:
    """JSON form value for create-human-job, or ``\"\"`` when email is missing."""
    fields = requester_surrogate_custom_fields(poster_email)
    return json.dumps(fields) if fields else ""


async def get_ht_service_account_ray_client(
    *,
    slack_user_id: str,
    slack_team_id: str,
    slack_enterprise_id: str | None,
) -> RayClient | None:
    """Mint a Verify/LC JWT for the configured IBM HT service member."""
    member_uuid = ht_service_account_member_uuid()
    sql = text(
        """
        SELECT m.obj_uuid, m.given_name, m.family_name, m.email_primary,
               m.login, m.groupid, m.active
        FROM obj_m_member m
        WHERE m.obj_uuid = :member_uuid
        LIMIT 1
        """
    ).bindparams(member_uuid=member_uuid)
    row = await fetch_one(sql, async_engines["sitemanager_readonly"])
    if not row:
        logger.error(
            "IBM HT service account member not found",
            extra={"member_uuid": member_uuid},
        )
        return None

    id_token = create_languagecloud_id_token(
        uuid=row["obj_uuid"],
        given_name=row["given_name"] or "",
        family_name=row["family_name"] or "",
        email=row["email_primary"] or "",
        is_active=bool(row["active"]),
        aud="languagecloud-api",
        secret=config.languagecloud_api_key.get_secret_value(),
    )
    return RayClient(
        id=row["obj_uuid"],
        user_group_id=row["groupid"] or "",
        username=row["login"] or row["email_primary"] or "",
        access_token="",
        slack_user_id=slack_user_id,
        slack_team_id=slack_team_id,
        slack_enterprise_id=slack_enterprise_id,
        slack_access_token=None,
        settings_id=None,
        id_token=id_token,
        planname=None,
        sso=False,
    )


async def resolve_ht_verify_client(
    ray: RayConnection | None,
    *,
    slack_user_id: str,
    slack_team_id: str,
    slack_enterprise_id: str | None,
) -> RayClient | None:
    """Prefer an active CRM member; otherwise IBM service account when eligible."""
    if ray is not None and ray.client is not None:
        return ray.client
    if should_use_ht_service_account(slack_enterprise_id, ray):
        return await get_ht_service_account_ray_client(
            slack_user_id=slack_user_id,
            slack_team_id=slack_team_id,
            slack_enterprise_id=slack_enterprise_id,
        )
    return None


async def resolve_ht_verify_client_for_job(
    ray: RayConnection | None,
    *,
    slack_user_id: str,
    slack_team_id: str,
    slack_enterprise_id: str | None,
    get_job,
    job_uuid: str,
) -> tuple[RayClient, Any]:
    """Resolve HT client and load the evaluate job.

    Prefers the poster's CRM client. If that cannot access the job (e.g. Admin
    accepting an HT job owned by the service account), falls back to the
    service-account JWT for IBM workspaces.
    """
    from app.api.verify import VerifyAPIError

    primary = await resolve_ht_verify_client(
        ray,
        slack_user_id=slack_user_id,
        slack_team_id=slack_team_id,
        slack_enterprise_id=slack_enterprise_id,
    )
    if primary is None:
        raise VerifyAPIError("No Verify client available for HT", 401)

    try:
        job = await get_job(primary, job_uuid)
        return primary, job
    except VerifyAPIError:
        if (
            not is_ibm_enterprise(slack_enterprise_id)
            or ray is None
            or not ray.super_group
            or primary.id == ht_service_account_member_uuid()
        ):
            raise
        service = await get_ht_service_account_ray_client(
            slack_user_id=slack_user_id,
            slack_team_id=slack_team_id,
            slack_enterprise_id=slack_enterprise_id,
        )
        if service is None:
            raise
        job = await get_job(service, job_uuid)
        return service, job


async def resolve_slack_poster_email(
    client: Any,
    slack_user_id: str,
) -> str:
    """Best-effort Slack profile email for Requester/Surrogate stamps."""
    if not slack_user_id:
        return ""
    try:
        user_info = await client.users_info(user=slack_user_id)
        profile = (user_info.get("user") or {}).get("profile") or {}
        return (profile.get("email") or "").strip()
    except Exception:
        logger.warning(
            "Failed to resolve Slack poster email for HT service-account stamp",
            extra={"slack_user_id": slack_user_id},
            exc_info=True,
        )
        return ""
