# RootLens Backend

The core API service for **RootLens: AI-Powered Enterprise Log Intelligence & Root Cause Analysis Platform**. Built with Python, FastAPI, and SQLAlchemy 2.x (asyncio).

---

## 1. Directory Structure

```
backend/
├── alembic/                 # Database migrations (Alembic)
│   ├── env.py               # Async migration runner
│   ├── script.py.mako       # Migration template
│   └── versions/            # Version-controlled migration scripts
│       └── 0001_initial_schema.py
├── alembic.ini              # Alembic configuration
├── app/
│   ├── __init__.py
│   ├── main.py              # Application entrypoint & middleware configuration
│   ├── api/                 # API routing architecture
│   │   ├── __init__.py
│   │   └── v1/              # Version 1 API
│   │       ├── __init__.py
│   │       ├── router.py    # Centralized v1 router
│   │       └── endpoints/   # Individual endpoint handlers
│   │           ├── __init__.py
│   │           └── health.py
│   ├── core/                # Core configuration & settings
│   │   ├── __init__.py
│   │   └── config.py        # Environment-driven Pydantic settings & DB URLs
│   ├── db/                  # Database connectivity
│   │   ├── __init__.py
│   │   ├── base.py          # Declarative Base
│   │   └── session.py       # Async engine & session lifecycle (lazy)
│   └── models/              # SQLAlchemy 2.x declarative domain models
│       ├── __init__.py
│       ├── log_source.py
│       ├── upload_job.py
│       ├── incident.py
│       ├── incident_affected_service.py
│       ├── incident_event.py
│       ├── evidence.py
│       ├── recommendation.py
│       └── report.py
├── tests/                   # Automated backend test suite
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_db_config.py    # Database configuration & session lifecycle tests
│   ├── test_health.py       # Health check & documentation endpoint tests
│   └── test_models.py       # Model metadata, foreign key & constraint tests
├── requirements.txt         # Direct dependencies
└── README.md                # Backend setup & developer instructions
```

---

## 2. Prerequisites

- Python 3.10 or higher (Python 3.12 recommended)
- `pip` package manager
- (Optional for live persistence) PostgreSQL 15+

---

## 3. Environment Setup & Installation

From the project root (`RootLens/`) or within `backend/`:

```bash
# Navigate to backend directory
cd backend

# Create dedicated virtual environment (if not already created)
python -m venv .venv

# Activate virtual environment
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Windows (CMD):
.venv\Scripts\activate.bat
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 4. Running the Development Server

Start the local Uvicorn development server:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Once running:
- **Root Health Check**: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- **API v1 Health Check**: [http://127.0.0.1:8000/api/v1/health](http://127.0.0.1:8000/api/v1/health)
- **Interactive Swagger UI**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc UI**: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

*Note: The application does not require a running PostgreSQL instance to start up or serve health check endpoints.*

---

## 5. Database Migrations (Alembic)

Alembic is configured with asynchronous driver support (`asyncpg`) and connects to `settings.async_database_url`.

```bash
# View migration history
alembic history

# Generate static SQL preview offline (without connecting to DB):
alembic upgrade head --sql

# Apply migrations to a live PostgreSQL database:
alembic upgrade head

# Roll back last migration:
alembic downgrade -1
```

---

## 6. Running Automated Tests

Run the full automated test suite with pytest:

```bash
pytest -v
```

The test suite includes:
- **Health & API Tests** (`tests/test_health.py`): Endpoint status, JSON schema, and OpenAPI specification.
- **Database Configuration Tests** (`tests/test_db_config.py`): Environment URL resolution, scheme upgrades, and lazy engine evaluation.
- **Model Metadata & Constraint Tests** (`tests/test_models.py`): Table registration, UUID primary keys, foreign key deletion rules, uniqueness constraints, confidence/confirmation decoupling, and in-memory schema compilation.

---

## 7. Configuration Reference

Settings are managed via `app.core.config.Settings` and loaded from environment variables (or optional `.env` file):

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `PROJECT_NAME` | string | `RootLens API` | Application title shown in docs |
| `VERSION` | string | `0.1.0` | API version string |
| `API_V1_STR` | string | `/api/v1` | Centralized prefix for version 1 routes |
| `ENVIRONMENT` | string | `development` | Deployment environment name |
| `DEBUG` | boolean | `True` | Enables detailed error payloads in development |
| `HOST` | string | `127.0.0.1` | Local server bind address |
| `PORT` | integer | `8000` | Local server bind port |
| `BACKEND_CORS_ORIGINS` | list/str | `http://localhost:3000,http://127.0.0.1:3000` | Allowed frontend origins |
| `POSTGRES_HOST` | string | `localhost` | PostgreSQL host |
| `POSTGRES_PORT` | integer | `5432` | PostgreSQL port |
| `POSTGRES_DB` | string | `rootlens` | PostgreSQL database name |
| `POSTGRES_USER` | string | `rootlens_user` | PostgreSQL username |
| `POSTGRES_PASSWORD` | string | `your_postgres_password_here` | PostgreSQL password |
| `DATABASE_URL` | string | `None` | Optional direct database URL override |
