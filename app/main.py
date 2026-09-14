# pyrefly: ignore [missing-import]
from fastapi import FastAPI, Request
# pyrefly: ignore [missing-import]
from fastapi.responses import HTMLResponse
# pyrefly: ignore [missing-import]
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.api.health import router as health_router
from app.config import settings

app = FastAPI(
    title=settings.app_name,
    description="Event-sourced, double-entry ledger service.",
    version="0.1.0",
)

app.include_router(health_router)

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

@app.get("/", response_class=HTMLResponse)
async def read_overview(request: Request):
    return templates.TemplateResponse(request=request, name="overview.html")

@app.get("/event-log", response_class=HTMLResponse)
async def read_event_log(request: Request):
    return templates.TemplateResponse(request=request, name="event_log.html")

@app.get("/post-transaction", response_class=HTMLResponse)
async def read_post_transaction(request: Request):
    return templates.TemplateResponse(request=request, name="post_transaction.html")

@app.get("/transaction-detail", response_class=HTMLResponse)
async def read_transaction_detail(request: Request):
    return templates.TemplateResponse(request=request, name="transaction_detail.html")
