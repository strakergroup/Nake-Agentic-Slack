import os
import sys
import requests
from sqlalchemy import text
from time import sleep

# Add the parent directory to sys.path
parent_dir = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "slack-ray-translator")
)
sys.path.append(parent_dir)

from app.database import engines

# Use the ray_integration engine from database.py
with open("update.sql", "w", buffering=1) as sql_file:

    query = """
    SELECT channel_id
    FROM slack_group_settings_translation
    GROUP BY channel_id
    HAVING COUNT(*) > 1;
    """
    duplicated_channel_ids = []
    # Execute the query to get duplicated channel_ids
    with engines["ray_integration"].connect() as conn:
        duplicated_channel_ids = conn.execute(text(query)).fetchall()

        # Step 2: For each duplicated channel_id, find the oldest entry and delete it
        for row in duplicated_channel_ids:
            channel_id = row.channel_id

            # Find the oldest entry for the duplicated channel_id
            oldest_entry_query = """
            SELECT id
            FROM slack_group_settings_translation
            WHERE channel_id = :channel_id
            ORDER BY created_at ASC
            LIMIT 1;
            """
            oldest_entry = conn.execute(
                text(oldest_entry_query).bindparams(channel_id=channel_id)
            ).fetchone()

            if oldest_entry:
                # Delete the oldest entry
                delete_query = """
                DELETE FROM slack_group_settings_translation
                WHERE id = :id;
                """
                bound_query = text(delete_query).bindparams(id=oldest_entry.id)
                compiled_query = (
                    str(
                        bound_query.compile(
                            dialect=conn.engine.dialect,
                            compile_kwargs={"literal_binds": True},
                        )
                    )
                    + ";\n"
                )
                sql_file.write(compiled_query)
                print(
                    f"Deleted oldest entry with id {oldest_entry.id} for channel_id {channel_id}"
                )

    # Define the raw SQL query
    query = """
    SELECT
        slack_group_settings.slack_enterprise_id,
        slack_group_settings.slack_team_id,
        slack_group_settings_translation.channel_id
    FROM
        slack_group_settings
        JOIN slack_group_settings_translation ON slack_group_settings_translation.settings_id = slack_group_settings.id;
    """
    query_count = 40
    iteration = 0
    # Open the file in write mode
    generated_statements = set()
    with engines["ray_integration"].connect() as conn:
        # Execute the query
        result = conn.execute(text(query))

        # Fetch and print the results
        for row in result:
            if not row.slack_enterprise_id:
                continue
            print(
                "checking channel_id",
                row.channel_id,
                row.slack_team_id,
                row.slack_enterprise_id,
            )
            query = """
                SELECT bot_token, enterprise_id
                FROM slack_bots
                WHERE team_id = :team_id
                ORDER BY id DESC
                LIMIT 1;
            """
            bound_query = text(query).bindparams(team_id=row.slack_team_id)
            bot_token = conn.execute(bound_query).fetchone()
            if not bot_token:
                continue
            # Make Slack Web API request to get channel info
            headers = {"Authorization": f"Bearer {bot_token.bot_token}"}
            channel_info_response = requests.get(
                "https://slack.com/api/conversations.info",
                headers=headers,
                params={"channel": row.channel_id},
            )
            # iteration += 1
            # if iteration % query_count == 0:
            #     sleep(60)  # Sleep for 1 minute
            channel_info = channel_info_response.json()
            team_id = channel_info.get("channel", {}).get("context_team_id", None)
            if not team_id:
                query = """
                    SELECT DISTINCT bot_token, enterprise_id
                    FROM slack_bots
                    WHERE team_id <> :team_id
                    AND enterprise_id = :enterprise_id
                    ORDER BY id DESC;
                """
                bound_query = text(query).bindparams(
                    team_id=row.slack_team_id, enterprise_id=bot_token.enterprise_id
                )
                all_tokens = conn.execute(bound_query).fetchall()
                for token in all_tokens:
                    headers = {"Authorization": f"Bearer {token.bot_token}"}
                    channel_info_response = requests.get(
                        "https://slack.com/api/conversations.info",
                        headers=headers,
                        params={"channel": row.channel_id},
                    )
                    channel_info = channel_info_response.json()
                    team_id = channel_info.get("channel", {}).get(
                        "context_team_id", None
                    )
                    if team_id:
                        break
            if not team_id:
                print("no team found for channel_id", row.channel_id)
                # delete
                query = """
                    DELETE FROM slack_group_settings_translation
                    WHERE channel_id = :channel_id
                """
                bound_query = text(query).bindparams(channel_id=row.channel_id)
                compiled_query = (
                    str(
                        bound_query.compile(
                            dialect=conn.engine.dialect,
                            compile_kwargs={"literal_binds": True},
                        )
                    )
                    + ";\n"
                )
                if compiled_query not in generated_statements:
                    sql_file.write(compiled_query)
                    generated_statements.add(compiled_query)
                continue
