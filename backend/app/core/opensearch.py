import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import anyio
from opensearchpy import OpenSearch
from app.core.config import Settings, settings

logger = logging.getLogger("rootlens.opensearch")

# Singleton OpenSearch client instance for lazy initialization
_client: Optional[OpenSearch] = None


def create_opensearch_client(custom_settings: Optional[Settings] = None) -> OpenSearch:
    """Create a configured OpenSearch client instance.
    
    Uses application settings with connection pooling and timeouts.
    Does not verify connectivity at instantiation time.
    """
    s = custom_settings or settings
    client = OpenSearch(
        hosts=s.opensearch_hosts,
        http_auth=s.opensearch_auth,
        use_ssl=s.OPENSEARCH_USE_SSL,
        verify_certs=s.OPENSEARCH_VERIFY_CERTS,
        ssl_show_warn=s.OPENSEARCH_VERIFY_CERTS,
        timeout=s.OPENSEARCH_TIMEOUT,
        max_retries=1,
    )
    return client


def get_opensearch_client() -> OpenSearch:
    """Return or lazily create the global OpenSearch client singleton."""
    global _client
    if _client is None:
        _client = create_opensearch_client()
    return _client


def close_opensearch_client() -> None:
    """Close and reset the OpenSearch client if initialized."""
    global _client
    if _client is not None:
        try:
            _client.close()
            logger.info("OpenSearch client connection closed successfully.")
        except Exception as exc:
            logger.warning("Error closing OpenSearch client: %s", exc)
        finally:
            _client = None


def verify_opensearch_connection(client: Optional[OpenSearch] = None) -> Dict[str, Any]:
    """Verify connectivity to the OpenSearch cluster synchronously.
    
    Never exposes passwords, authentication headers, or sensitive connection parameters.
    """
    target_client = client or get_opensearch_client()
    try:
        if not target_client.ping():
            return {
                "status": "unavailable",
                "error": "OpenSearch ping returned false",
            }
        info = target_client.info()
        return {
            "status": "available",
            "cluster_name": info.get("cluster_name", "unknown"),
            "cluster_uuid": info.get("cluster_uuid"),
            "version": info.get("version", {}).get("number", "unknown"),
            "distribution": info.get("version", {}).get("distribution", "opensearch"),
        }
    except Exception:
        # Non-sensitive error response; hides host credentials and internal stack traces
        return {
            "status": "unavailable",
            "error": "OpenSearch cluster unreachable",
        }


async def check_opensearch_health(client: Optional[OpenSearch] = None) -> Dict[str, Any]:
    """Asynchronously verify OpenSearch cluster health using thread pool.
    
    Prevents blocking the async event loop during readiness checks.
    """
    return await anyio.to_thread.run_sync(verify_opensearch_connection, client)


def get_log_index_mapping() -> Dict[str, Any]:
    """Return the canonical mapping schema for normalized RootLens log documents.
    
    Adheres to docs/architecture.md and docs/engineering-decisions.md (ADR-1 & ADR-2):
    - timestamp: date formatted for high-resolution sorting and range filtering
    - service_name, environment, log_level: keyword fields for exact filtering
    - message: analyzed text for full-text search with keyword subfield for aggregations
    - host, trace_id, span_id: keyword fields for distributed tracing correlation
    - attributes: dynamic JSON object for application-specific structured metadata
    - raw_log: preserved original log string (unindexed to optimize storage/search performance)
    """
    return {
        "properties": {
            "timestamp": {
                "type": "date",
                "format": "strict_date_optional_time||epoch_millis",
            },
            "service_name": {
                "type": "keyword",
            },
            "environment": {
                "type": "keyword",
            },
            "log_level": {
                "type": "keyword",
            },
            "message": {
                "type": "text",
                "fields": {
                    "keyword": {
                        "type": "keyword",
                        "ignore_above": 512,
                    }
                },
            },
            "host": {
                "type": "keyword",
            },
            "trace_id": {
                "type": "keyword",
            },
            "span_id": {
                "type": "keyword",
            },
            "attributes": {
                "type": "object",
                "dynamic": True,
            },
            "raw_log": {
                "type": "text",
                "index": False,
            },
        }
    }


def get_log_index_template(index_prefix: Optional[str] = None) -> Dict[str, Any]:
    """Return the reusable index template for time-partitioned RootLens log indices.
    
    Targets indices matching '{index_prefix}-*' (e.g. rootlens-logs-2026.10).
    """
    prefix = index_prefix or settings.OPENSEARCH_INDEX_PREFIX
    return {
        "index_patterns": [f"{prefix}-*"],
        "template": {
            "settings": {
                "index": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    "refresh_interval": "1s",
                }
            },
            "mappings": get_log_index_mapping(),
        },
        "priority": 100,
        "_meta": {
            "description": "RootLens normalized log document index template",
            "version": "1.0.0",
        },
    }


def ensure_log_index_template(
    client: Optional[OpenSearch] = None,
    template_name: str = "rootlens_logs_template",
) -> Dict[str, Any]:
    """Ensure that the normalized log index template is installed in OpenSearch.
    
    Safe and idempotent. Never modifies existing indexes or destroys data.
    """
    target_client = client or get_opensearch_client()
    template_body = get_log_index_template()
    try:
        res = target_client.indices.put_index_template(
            name=template_name,
            body=template_body,
        )
        return {"status": "acknowledged", "template_name": template_name, "response": res}
    except Exception as exc:
        return {"status": "error", "template_name": template_name, "error": str(exc)}


def resolve_log_index_name(
    dt: Optional[datetime] = None,
    index_prefix: Optional[str] = None,
) -> str:
    """Resolve target time-partitioned index name for a given log timestamp.
    
    Format: '{prefix}-{YYYY.MM}' (e.g. 'rootlens-logs-2026.10').
    Defaults to current UTC month if timestamp is missing.
    """
    prefix = index_prefix or settings.OPENSEARCH_INDEX_PREFIX
    target_dt = dt or datetime.now(timezone.utc)
    return f"{prefix}-{target_dt.strftime('%Y.%m')}"


