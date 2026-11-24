from contextlib import asynccontextmanager
import logging
from typing import Any, Dict
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.endpoints.health import get_readiness_payload
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.opensearch import close_opensearch_client
from app.db.session import close_engine

logger = logging.getLogger("rootlens")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and clean resource shutdown."""
    logger.info("Initializing RootLens API lifecycle...")
    yield
    logger.info("Shutting down RootLens API: cleaning up database and search resources...")
    await close_engine()
    close_opensearch_client()
    logger.info("RootLens API shutdown complete.")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="RootLens: AI-Powered Enterprise Log Intelligence & Root Cause Analysis Platform API",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Configure Cross-Origin Resource Sharing (CORS)
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unhandled exceptions while preserving development debug visibility."""
    logger.exception("Unhandled server error processing request to %s", request.url)
    payload: Dict[str, Any] = {
        "status": "error",
        "message": "Internal server error occurred.",
    }
    if settings.DEBUG:
        payload["detail"] = str(exc)
        payload["error_type"] = type(exc).__name__

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=payload,
    )


@app.get("/health", tags=["health"], summary="Root Health Check")
def root_health() -> Dict[str, str]:
    """Truthful root-level health check endpoint."""
    return {
        "status": "healthy",
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
    }


@app.get("/ready", tags=["health"], summary="Root Readiness Check")
async def root_ready() -> JSONResponse:
    """Check readiness of backing services (PostgreSQL and OpenSearch)."""
    payload = await get_readiness_payload()
    http_status = (
        status.HTTP_200_OK
        if payload["status"] in ("ready", "degraded")
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(status_code=http_status, content=payload)


# Centralized versioned API router mount
app.include_router(api_router, prefix=settings.API_V1_STR)
