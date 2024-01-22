from sqlalchemy import text  # type: ignore

from ..auth.connector import RayClient
from ..database import engines


def get_auto_translate_settings_conversations(ray_client: RayClient) -> list[str]:
    """Get the auto-translate settings (conversations) for a LanugageCloud user.

    Returns:
        list[str]: The list of conversation IDs to auto-translate.
    """
    with engines["ray_integration"].connect() as conn:
        sql = text(
            """
            SELECT conversation_id
            FROM slack_settings_auto_translate_conversations
            WHERE member_uuid = :member_uuid
            """
        ).bindparams(member_uuid=ray_client.id)
        result = conn.execute(sql)
        conversation_ids = [row[0] for row in result]
    return conversation_ids


def get_auto_translate_settings_langs(ray_client: RayClient) -> list[str]:
    """Get the auto-translate settings (languages) for a LanugageCloud user.

    Returns:
        list[str]: The list of languages to auto-translate to.
    """
    with engines["ray_integration"].connect() as conn:
        sql = text(
            """
            SELECT lang
            FROM slack_settings_auto_translate_langs
            WHERE member_uuid = :member_uuid
            """
        ).bindparams(member_uuid=ray_client.id)
        result = conn.execute(sql)
        langs = [row[0] for row in result]
    return langs


def update_auto_translate_settings(
    ray_client: RayClient, conversations: list[str], languages: list[str]
) -> None:
    """Update the auto-translate settings for a LanugageCloud user.

    Args:
        ray_client (RayClient): The client to update the settings for
        conversations (list[str]): The IDs of the conversations to auto-translate.
        languages (list[str]): The languages to auto-translate to.
    """
    with engines["ray_integration"].begin() as conn:
        conn.execute(
            text(
                """
                DELETE FROM slack_settings_auto_translate_conversations
                WHERE member_uuid = :member_uuid
                """
            ).bindparams(member_uuid=ray_client.id)
        )
        conn.execute(
            text(
                """
                DELETE FROM slack_settings_auto_translate_langs
                WHERE member_uuid = :member_uuid
                """
            ).bindparams(member_uuid=ray_client.id)
        )
        for conversation in conversations:
            conn.execute(
                text(
                    """
                    INSERT INTO slack_settings_auto_translate_conversations
                    (member_uuid, conversation_id)
                    VALUES
                    (:member_uuid, :conversation_id)
                    """
                ).bindparams(member_uuid=ray_client.id, conversation_id=conversation)
            )
        for lang in languages:
            conn.execute(
                text(
                    """
                    INSERT INTO slack_settings_auto_translate_langs
                    (member_uuid, lang)
                    VALUES
                    (:member_uuid, :lang)
                    """
                ).bindparams(member_uuid=ray_client.id, lang=lang)
            )
