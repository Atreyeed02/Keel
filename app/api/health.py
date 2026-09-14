from fastapi import APIRouter

from app.db.engine import ping

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Liveness + DB connectivity check. Never raises — reports status instead."""
    db_ok = await ping()
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "up" if db_ok else "down",
    }
