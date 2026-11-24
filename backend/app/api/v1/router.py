from fastapi import APIRouter
from app.api.v1.endpoints import health, incidents, logs

api_router = APIRouter()

# Register endpoint routers under API v1
api_router.include_router(health.router, tags=["health"])
api_router.include_router(logs.router, prefix="/logs", tags=["logs"])
api_router.include_router(incidents.router, prefix="/incidents", tags=["incidents"])
