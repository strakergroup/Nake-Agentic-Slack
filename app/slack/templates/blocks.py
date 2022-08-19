"""Templates for individual Slack blocks."""

from typing import Any

from ...ray.utils import get_job_url


def job_deltaray_link_block(job_uuid: str, client_id: str) -> dict[str, Any]:
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "View this job in deltaRAY",
                    "emoji": True,
                },
                "url": get_job_url(job_uuid, client_id),
                "action_id": "link",
            }
        ],
    }
