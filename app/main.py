from fastapi import FastAPI
from .routers import slack, ray


# Configure FastAPI
app = FastAPI()
app.include_router(slack.router)
app.include_router(ray.router)


@app.get("/")
async def root():
    return {"message": "Slack Ray Translator App"}
