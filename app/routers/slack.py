import os
from fastapi import APIRouter, Request
from ..slack import slack_handler


# Connect the Slack Bolt endpoints to FastAPI
router = APIRouter(tags=['slack'])


@router.api_route('/slack/{path:path}', methods=['GET', 'POST'])
async def slack(request: Request):
    return await slack_handler.handle(request)
