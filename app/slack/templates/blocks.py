"""Templates for individual Slack blocks."""
# Ignore line too long lint errors
# flake8: noqa

from typing import Any
from urllib.parse import urlencode

from ...config import config


def job_deltaray_link_block(job_id: str, client_id: str) -> dict[str, Any]:
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
                "url": f"{config.deltaray_domain}/job/detail?{urlencode({'j': job_id, 'member_id': client_id})}",
                "action_id": "link",
            }
        ],
    }
