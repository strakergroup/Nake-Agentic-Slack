import os
import sys
import requests
from sqlalchemy import text

# Add the parent directory to sys.path
parent_dir = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "slack-ray-translator")
)
sys.path.append(parent_dir)

from app.database import engines

# Use the ray_integration engine from database.py

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

# Open the file in write mode
with open("update.sql", "w") as sql_file:
    generated_statements = set()
    with engines["ray_integration"].connect() as conn:
        # Execute the query
        result = conn.execute(text(query))

        # Fetch and print the results
        for row in result:
            query = """
                SELECT bot_token
                FROM slack_bots
                WHERE enterprise_id = :enterprise_id
                ORDER BY id DESC
                LIMIT 1;
            """
            bound_query = text(query).bindparams(enterprise_id=row.slack_enterprise_id)
            bot_token = conn.execute(bound_query).fetchone()

            # Make Slack Web API request to get channel info
            headers = {"Authorization": f"Bearer {bot_token.bot_token}"}
            channel_info_response = requests.get(
                "https://slack.com/api/conversations.info",
                headers=headers,
                params={"channel": row.channel_id},
            )
            channel_info = channel_info_response.json()
            if channel_info["channel"]["context_team_id"] != row.slack_team_id:
                query = """
                   SELECT id FROM slack_group_settings
                    WHERE slack_team_id = :team_id
                """
                bound_query = text(query).bindparams(
                    team_id=channel_info["channel"]["context_team_id"]
                )
                settings_id = conn.execute(bound_query).fetchone()

                if settings_id is None:
                    query = """
                        INSERT INTO slack_group_settings (slack_team_id, slack_enterprise_id)
                        VALUES (:team_id, :enterprise_id)
                    """
                    bound_query = text(query).bindparams(
                        team_id=channel_info["channel"]["context_team_id"],
                        enterprise_id=row.slack_enterprise_id,
                    )
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
                query = """
                    UPDATE slack_group_settings_translation
                    SET settings_id = (SELECT id FROM slack_group_settings WHERE slack_team_id = :team_id)
                    WHERE channel_id = :channel_id
                """
                bound_query = text(query).bindparams(
                    channel_id=row.channel_id,
                    team_id=channel_info["channel"]["context_team_id"],
                )
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
