import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.opensearch import (
    close_opensearch_client,
    create_opensearch_client,
    ensure_log_index_template,
    get_log_index_mapping,
    get_log_index_template,
    get_opensearch_client,
    verify_opensearch_connection,
)
from app.main import app

client = TestClient(app)


def test_opensearch_settings_defaults():
    """Verify OpenSearch settings defaults and helper properties."""
    # Test pure defaults without env file interference
    defaults = Settings(_env_file=None, OPENSEARCH_USER=None, OPENSEARCH_PASSWORD=None)
    assert defaults.OPENSEARCH_HOST == "localhost"
    assert defaults.OPENSEARCH_PORT == 9200
    assert defaults.OPENSEARCH_USE_SSL is False
    assert defaults.OPENSEARCH_VERIFY_CERTS is False
    assert defaults.OPENSEARCH_INDEX_PREFIX == "rootlens-logs"
    assert defaults.OPENSEARCH_TIMEOUT == 5

    assert defaults.opensearch_hosts == [{"host": "localhost", "port": 9200}]
    assert defaults.opensearch_auth is None

    custom = Settings(
        OPENSEARCH_HOST="opensearch.internal",
        OPENSEARCH_PORT=9201,
        OPENSEARCH_USER="admin_user",
        OPENSEARCH_PASSWORD="secret_password",
    )
    assert custom.opensearch_hosts == [{"host": "opensearch.internal", "port": 9201}]
    assert custom.opensearch_auth == ("admin_user", "secret_password")


def test_opensearch_auth_empty_or_missing_credentials():
    """Verify missing, empty, or whitespace-only credentials result in opensearch_auth being None."""
    assert Settings(_env_file=None, OPENSEARCH_USER=None, OPENSEARCH_PASSWORD=None).opensearch_auth is None
    assert Settings(_env_file=None, OPENSEARCH_USER="", OPENSEARCH_PASSWORD="").opensearch_auth is None
    assert Settings(_env_file=None, OPENSEARCH_USER="   ", OPENSEARCH_PASSWORD="   ").opensearch_auth is None
    assert Settings(_env_file=None, OPENSEARCH_USER="admin", OPENSEARCH_PASSWORD="").opensearch_auth is None
    assert Settings(_env_file=None, OPENSEARCH_USER="", OPENSEARCH_PASSWORD="secret").opensearch_auth is None


def test_create_opensearch_client_auth_propagation():
    """Verify create_opensearch_client propagates None when unconfigured and auth tuple when configured."""
    from unittest.mock import patch

    with patch("app.core.opensearch.OpenSearch") as mock_os:
        # Without credentials
        no_auth_settings = Settings(_env_file=None, OPENSEARCH_USER="", OPENSEARCH_PASSWORD="")
        create_opensearch_client(custom_settings=no_auth_settings)
        assert mock_os.call_args[1]["http_auth"] is None

        # With credentials
        auth_settings = Settings(_env_file=None, OPENSEARCH_USER="admin_user", OPENSEARCH_PASSWORD="secret_password")
        create_opensearch_client(custom_settings=auth_settings)
        assert mock_os.call_args[1]["http_auth"] == ("admin_user", "secret_password")


def test_opensearch_client_lazy_singleton():
    """Verify lazy creation and clean closing of the OpenSearch client singleton."""
    close_opensearch_client()
    c1 = get_opensearch_client()
    assert c1 is not None

    c2 = get_opensearch_client()
    assert c1 is c2

    close_opensearch_client()
    c3 = get_opensearch_client()
    assert c3 is not None
    close_opensearch_client()


def test_opensearch_connection_verification_available():
    """Verify connection verification returns available status when ping and info succeed."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.info.return_value = {
        "cluster_name": "rootlens-cluster",
        "cluster_uuid": "mock-uuid-123",
        "version": {
            "number": "2.11.0",
            "distribution": "opensearch",
        },
    }

    result = verify_opensearch_connection(client=mock_client)
    assert result["status"] == "available"
    assert result["cluster_name"] == "rootlens-cluster"
    assert result["version"] == "2.11.0"
    assert result["distribution"] == "opensearch"


def test_opensearch_connection_verification_ping_false():
    """Verify connection verification returns unavailable when ping returns False."""
    mock_client = MagicMock()
    mock_client.ping.return_value = False

    result = verify_opensearch_connection(client=mock_client)
    assert result["status"] == "unavailable"
    assert "ping returned false" in result["error"]


def test_opensearch_connection_verification_exception():
    """Verify connection verification gracefully handles exceptions without leaking stack trace."""
    mock_client = MagicMock()
    mock_client.ping.side_effect = ConnectionError("Internal socket connection refused")

    result = verify_opensearch_connection(client=mock_client)
    assert result["status"] == "unavailable"
    assert result["error"] == "OpenSearch cluster unreachable"


def test_log_index_mapping_structure():
    """Verify canonical mapping includes all required fields from docs/architecture.md."""
    mapping = get_log_index_mapping()
    properties = mapping.get("properties", {})

    # Required fields per Phase 06 specification
    expected_fields = {
        "timestamp",
        "service_name",
        "environment",
        "log_level",
        "message",
        "host",
        "trace_id",
        "span_id",
        "attributes",
        "raw_log",
    }
    assert expected_fields.issubset(set(properties.keys()))

    # Verify field types
    assert properties["timestamp"]["type"] == "date"
    assert properties["service_name"]["type"] == "keyword"
    assert properties["environment"]["type"] == "keyword"
    assert properties["log_level"]["type"] == "keyword"
    assert properties["message"]["type"] == "text"
    assert "keyword" in properties["message"]["fields"]
    assert properties["host"]["type"] == "keyword"
    assert properties["trace_id"]["type"] == "keyword"
    assert properties["span_id"]["type"] == "keyword"
    assert properties["attributes"]["type"] == "object"
    assert properties["attributes"]["dynamic"] is True
    assert properties["raw_log"]["type"] == "text"
    assert properties["raw_log"]["index"] is False


def test_log_index_template_structure():
    """Verify reusable index template wraps settings, pattern, and mapping correctly."""
    template = get_log_index_template(index_prefix="test-logs")
    assert template["index_patterns"] == ["test-logs-*"]
    assert template["priority"] == 100
    assert "template" in template

    inner = template["template"]
    assert "settings" in inner
    assert inner["settings"]["index"]["number_of_shards"] == 1
    assert inner["settings"]["index"]["number_of_replicas"] == 0
    assert inner["settings"]["index"]["refresh_interval"] == "1s"

    assert "mappings" in inner
    assert "properties" in inner["mappings"]


def test_readiness_endpoint_both_services_available(monkeypatch):
    """Verify GET /api/v1/ready and /ready return 200 with status 'ready' when both stores respond."""
    async def mock_db():
        return {"status": "available", "database": "rootlens"}

    async def mock_os():
        return {
            "status": "available",
            "cluster_name": "rootlens-os",
            "version": "2.11.0",
        }

    monkeypatch.setattr("app.api.v1.endpoints.health.check_database_health", mock_db)
    monkeypatch.setattr("app.api.v1.endpoints.health.check_opensearch_health", mock_os)

    # Test versioned endpoint
    v1_res = client.get("/api/v1/ready")
    assert v1_res.status_code == 200
    data = v1_res.json()
    assert data["status"] == "ready"
    assert data["dependencies"]["postgres"]["status"] == "available"
    assert data["dependencies"]["opensearch"]["status"] == "available"

    # Test root readiness endpoint
    root_res = client.get("/ready")
    assert root_res.status_code == 200
    root_data = root_res.json()
    assert root_data["status"] == "ready"


def test_readiness_endpoint_degraded_when_opensearch_unavailable(monkeypatch):
    """Verify GET /api/v1/ready returns 200 with status 'degraded' when OpenSearch is offline."""
    async def mock_db():
        return {"status": "available", "database": "rootlens"}

    async def mock_os():
        return {"status": "unavailable", "error": "OpenSearch cluster unreachable"}

    monkeypatch.setattr("app.api.v1.endpoints.health.check_database_health", mock_db)
    monkeypatch.setattr("app.api.v1.endpoints.health.check_opensearch_health", mock_os)

    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["dependencies"]["postgres"]["status"] == "available"
    assert data["dependencies"]["opensearch"]["status"] == "unavailable"


def test_readiness_endpoint_unhealthy_when_postgres_unavailable(monkeypatch):
    """Verify GET /api/v1/ready returns 503 with status 'unhealthy' when PostgreSQL is offline."""
    async def mock_db():
        return {"status": "unavailable", "error": "Database connection failed"}

    async def mock_os():
        return {"status": "available", "cluster_name": "mock-cluster"}

    monkeypatch.setattr("app.api.v1.endpoints.health.check_database_health", mock_db)
    monkeypatch.setattr("app.api.v1.endpoints.health.check_opensearch_health", mock_os)

    response = client.get("/api/v1/ready")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["dependencies"]["postgres"]["status"] == "unavailable"


def test_readiness_does_not_expose_credentials_or_urls(monkeypatch):
    """Verify readiness endpoints never leak passwords, secrets, or raw connection strings."""
    async def mock_db():
        return {"status": "available", "database": "rootlens"}

    async def mock_os():
        return {"status": "available", "cluster_name": "rootlens-cluster"}

    monkeypatch.setattr("app.api.v1.endpoints.health.check_database_health", mock_db)
    monkeypatch.setattr("app.api.v1.endpoints.health.check_opensearch_health", mock_os)

    response = client.get("/api/v1/ready")
    text_content = response.text
    assert "password" not in text_content.lower()
    assert "postgresql://" not in text_content
    assert "asyncpg" not in text_content
    assert "admin_user" not in text_content


def test_ensure_log_index_template_success():
    """Verify ensure_log_index_template puts template and returns acknowledged."""
    mock_client = MagicMock()
    mock_client.indices.put_index_template.return_value = {"acknowledged": True}

    res = ensure_log_index_template(client=mock_client, template_name="test_template")
    assert res["status"] == "acknowledged"
    assert res["template_name"] == "test_template"
    mock_client.indices.put_index_template.assert_called_once()


def test_ensure_log_index_template_error():
    """Verify ensure_log_index_template catches exceptions cleanly."""
    mock_client = MagicMock()
    mock_client.indices.put_index_template.side_effect = RuntimeError("OpenSearch permission denied")

    res = ensure_log_index_template(client=mock_client, template_name="test_template")
    assert res["status"] == "error"
    assert "permission denied" in res["error"]

