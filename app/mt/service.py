from sqlalchemy import text

from app.database import engines


def evaluate_get_org_groups(organization_uuid: str) -> list[str]:
    with engines["sitemanager"].connect() as conn:
        result = conn.execute(
            text("SELECT obj_uuid FROM obj_m_group WHERE organization_id = :org_uuid"),
            {"org_uuid": organization_uuid},
        )
        rows = result.fetchall()
        return [row.obj_uuid for row in rows]


def evaluate_get_glossary_resource(
    org_uuid: str, sl: str | None, tl: str | None, engine: str
) -> str:
    groups = evaluate_get_org_groups(org_uuid)

    if not groups:
        return ""

    with engines["machine_translation_readonly"].connect() as conn:
        result = conn.execute(
            text("""
                SELECT terminology_id
                FROM terminology_third_party_info
                WHERE group_id = ANY(:groups)
                AND sl = :sl
                AND tl = :tl
                AND terminology_engine = :engine
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"groups": groups, "sl": sl, "tl": tl, "engine": engine},
        )
        row = result.fetchone()
        if not row:
            return ""

        return row.terminology_id
