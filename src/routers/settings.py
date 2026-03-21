"""Settings API routes — runtime configuration for operational knobs."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import get_settings, update_settings

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsPatch(BaseModel):
    max_concurrent_runners: int = None  # type: ignore[assignment]
    min_spawn_interval: int = None  # type: ignore[assignment]
    task_timeout: int = None  # type: ignore[assignment]
    budget_daily_usd: float = None  # type: ignore[assignment]


@router.get("/settings")
async def read_settings():
    return get_settings()


@router.patch("/settings")
async def write_settings(body: SettingsPatch):
    patch = {k: v for k, v in body.dict().items() if v is not None}
    if not patch:
        return get_settings()
    try:
        result = update_settings(patch)
        return result
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
