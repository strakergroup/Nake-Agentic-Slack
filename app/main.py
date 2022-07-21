from fastapi import FastAPI
from .routers import slack


# Configure FastAPI
app = FastAPI()
app.include_router(slack.router)


@app.get('/')
async def root():
    return {'message': 'Slack Ray Translator App'}
