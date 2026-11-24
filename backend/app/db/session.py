from typing import Any, AsyncGenerator, Dict, Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

# Lazy engine and sessionmaker instances
_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine() -> AsyncEngine:
    """Return or lazily create the global async SQLAlchemy engine.
    
    Does not connect at import time, preventing startup errors when testing
    health endpoints without a live PostgreSQL instance.
    """
    global _engine
    if _engine is None:
        db_url = settings.async_database_url
        if not db_url:
            raise ValueError("Database connection URL is not configured.")
        _engine = create_async_engine(
            db_url,
            echo=settings.DEBUG and settings.ENVIRONMENT == "development",
            future=True,
            pool_pre_ping=True,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return or lazily create the global async sessionmaker."""
    global _sessionmaker
    if _sessionmaker is None:
        engine = get_engine()
        _sessionmaker = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async database session with automatic lifecycle cleanup."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def close_engine() -> None:
    """Close and dispose of the async SQLAlchemy engine if initialized."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None


async def check_database_health() -> Dict[str, Any]:
    """Verify PostgreSQL connectivity without exposing credentials or internal connection strings."""
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {
            "status": "available",
            "database": settings.POSTGRES_DB,
        }
    except Exception:
        return {
            "status": "unavailable",
            "error": "Database connection failed",
        }
