from sqlalchemy import text
from straker_utils.sql.async_engine import fetch_all, fetch_one

from app.database import async_engines


async def evaluate_get_org_groups(organization_uuid: str) -> list[str]:
    sql = text(
        "SELECT obj_uuid FROM obj_m_group WHERE organization_id = :org_uuid"
    ).bindparams(org_uuid=organization_uuid)
    result = await fetch_all(sql, async_engines["sitemanager"])
    return [row["obj_uuid"] for row in result]


async def evaluate_get_glossary_resource(
    org_uuid: str, sl: str | None, tl: str | None, engine: str
) -> str:
    groups = await evaluate_get_org_groups(org_uuid)

    if not groups:
        return ""

    sql = text("""
        SELECT terminology_id
        FROM terminology_third_party_info
        WHERE group_id = ANY(:groups)
        AND sl = :sl
        AND tl = :tl
        AND terminology_engine = :engine
        ORDER BY created_at DESC
        LIMIT 1
    """).bindparams(groups=groups, sl=sl, tl=tl, engine=engine)
    result = await fetch_one(sql, async_engines["machine_translation_readonly"])
    if not result:
        return ""

    return result["terminology_id"]
