import os
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

router = APIRouter(tags=["ops"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict[str, str]:
    checks: dict[str, str] = {}
    try:
        async with request.app.state.sessions() as session:
            await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {type(exc).__name__}"
    try:
        blobs = request.app.state.blobs
        checks["blob"] = "ok" if blobs is not None else "error: not configured"
    except Exception as exc:
        checks["blob"] = f"error: {type(exc).__name__}"
    checks["provider"] = "skipped"
    if any(value.startswith("error") for value in checks.values()):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return checks


@router.get("/version")
async def version_info() -> dict[str, str]:
    try:
        pkg_version = version("contacompa")
    except PackageNotFoundError:
        pkg_version = "0.0.0"
    return {
        "version": pkg_version,
        "git_sha": os.environ.get("APP_GIT_SHA", "unknown"),
        "build_time": os.environ.get("APP_BUILD_TIME", datetime.now(UTC).isoformat()),
    }
