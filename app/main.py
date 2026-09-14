from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import settings

app = FastAPI(
    title=settings.app_name,
    description="Event-sourced, double-entry ledger service.",
    version="0.1.0",
)

app.include_router(health_router)
