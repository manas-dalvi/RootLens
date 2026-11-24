from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_root_health_endpoint():
    """Verify GET /health returns 200 OK and expected structure."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["app"] == "RootLens API"
    assert "version" in data
    assert "environment" in data


def test_api_v1_health_endpoint():
    """Verify GET /api/v1/health returns 200 OK and expected versioned structure."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["app"] == "RootLens API"
    assert data["api_version"] == "v1"
    assert "version" in data


def test_docs_endpoint_accessible():
    """Verify Swagger UI /docs is available."""
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_json_schema():
    """Verify OpenAPI schema publishes title and registered paths."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "RootLens API"
    assert "/health" in schema["paths"]
    assert "/api/v1/health" in schema["paths"]
