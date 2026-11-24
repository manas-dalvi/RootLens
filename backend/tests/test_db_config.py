import inspect
import pytest
from app.core.config import Settings
from app.db.session import get_db, get_engine, get_sessionmaker


def test_async_database_url_resolution():
    """Verify async_database_url correctly resolves with asyncpg scheme."""
    custom_settings = Settings(
        DATABASE_URL=None,
        POSTGRES_HOST="10.0.0.1",
        POSTGRES_PORT=5433,
        POSTGRES_DB="testdb",
        POSTGRES_USER="testuser",
        POSTGRES_PASSWORD="testpassword",
    )
    url = custom_settings.async_database_url
    assert url.startswith("postgresql+asyncpg://")
    assert "testuser:testpassword@10.0.0.1:5433/testdb" in url


def test_async_database_url_converts_postgresql_scheme():
    """Verify that a standard postgresql:// URL is automatically upgraded to postgresql+asyncpg://."""
    custom_settings = Settings(
        DATABASE_URL="postgresql://user:pass@localhost:5432/rootlens"
    )
    assert custom_settings.async_database_url == "postgresql+asyncpg://user:pass@localhost:5432/rootlens"


def test_get_db_is_async_generator():
    """Verify that get_db is an asynchronous generator function suitable for FastAPI Depends."""
    assert inspect.isasyncgenfunction(get_db)


def test_engine_creation_is_lazy():
    """Verify engine and sessionmaker are created without raising an immediate connection error."""
    engine = get_engine()
    assert engine is not None
    assert str(engine.url).startswith("postgresql+asyncpg://")
    sessionmaker = get_sessionmaker()
    assert sessionmaker is not None
