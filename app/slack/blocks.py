from ..config import straker_config
from ..slack.auth import get_slack_deltaray_integration_url


def onboarding_block(user_id: str, team_id: str, app_id: str, channel_id: str):
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "The Straker RAY App has been sucessfully installed in your Slack workspace! :tada:"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Sign in to your DeltaRay account to use slash commands and receive notifications about your translation jobs."
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Connect DeltaRay account"
                    },
                    "style": "primary",
                    "url": get_slack_deltaray_integration_url(user_id, team_id, app_id, channel_id),
                    "action_id": "login"
                }
            ]
        }
    ]


def login_block(user_id: str, team_id: str, app_id: str, channel_id: str):
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Connect your DeltaRay account"
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Connect DeltaRay account"
                    },
                    "style": "primary",
                    "url": get_slack_deltaray_integration_url(user_id, team_id, app_id, channel_id),
                    "action_id": "login"
                }
            ]
        }
    ]


def successful_login_block(user_id: str, ray_username: str):
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Login was successful! <@{user_id}> is now connected with <{straker_config.deltaray_domain}|{ray_username}>."
            }
        }
    ]
