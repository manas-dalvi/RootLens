from typing import Any, Dict
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.opensearch import check_opensearch_health
from app.db.session import check_database_health

router = APIRouter()


async def get_readiness_payload() -> Dict[str, Any]:
    """Inspect and report the health of underlying backing stores.
    
    Reports PostgreSQL and OpenSearch independently.
    Never exposes credentials, secrets, or internal connection URLs.
    """
    db_health = await check_database_health()
    os_health = await check_opensearch_health()

    is_db_ready = db_health.get("status") == "available"
    is_os_ready = os_health.get("status") == "available"

    # PostgreSQL is required for relational metadata.
    # OpenSearch is optional during startup/early development phases.
    if is_db_ready and is_os_ready:
        overall_status = "ready"
    elif is_db_ready and not is_os_ready:
        overall_status = "degraded"
    else:
        overall_status = "unhealthy"

    return {
        "status": overall_status,
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "dependencies": {
            "postgres": db_health,
            "opensearch": os_health,
        },
    }


@router.get("/health", summary="API v1 Health Check")
def v1_health_check() -> Dict[str, str]:
    """Return truthful health status for the versioned API (v1)."""
    return {
        "status": "healthy",
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "api_version": "v1",
    }


@router.get("/ready", summary="API v1 Readiness Check")
async def v1_readiness_check() -> JSONResponse:
    """Return backing service readiness for PostgreSQL and OpenSearch."""
    payload = await get_readiness_payload()
    http_status = (
        status.HTTP_200_OK
        if payload["status"] in ("ready", "degraded")
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(status_code=http_status, content=payload)
