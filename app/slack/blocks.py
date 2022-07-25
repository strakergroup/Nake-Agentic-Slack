from ..slack.auth import get_slack_deltaray_integration_url


def onboarding_block(user_id: str, team_id: str, app_id: str):
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
                    "url": get_slack_deltaray_integration_url(user_id, team_id, app_id),
                    "action_id": "login"
                }
            ]
        }
    ]
