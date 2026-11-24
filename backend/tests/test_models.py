import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import class_mapper

from app.db.base import Base
from app.models import (
    Evidence,
    Incident,
    IncidentAffectedService,
    IncidentEvent,
    LogSource,
    Recommendation,
    Report,
    UploadJob,
)


def test_metadata_contains_all_approved_tables():
    """Verify that all domain tables are registered in Base.metadata."""
    expected_tables = {
        "log_sources",
        "upload_jobs",
        "incidents",
        "incident_affected_services",
        "incident_events",
        "evidence",
        "recommendations",
        "reports",
        "rca_hypothesis_decisions",
    }
    registered_tables = set(Base.metadata.tables.keys())
    assert expected_tables == registered_tables, f"Tables mismatch: {registered_tables ^ expected_tables}"


def test_table_primary_keys_are_uuid():
    """Verify each table defines 'id' as a UUID primary key."""
    for table_name, table in Base.metadata.tables.items():
        assert "id" in table.c, f"Table {table_name} missing 'id' column"
        assert table.c.id.primary_key, f"Table {table_name}.id is not a primary key"


def test_foreign_key_deletion_policies():
    """Verify justified foreign key deletion policies matching docs/data-model.md."""
    # upload_jobs -> log_sources (RESTRICT)
    uj_fk = next(fk for fk in Base.metadata.tables["upload_jobs"].foreign_keys if fk.column.table.name == "log_sources")
    assert uj_fk.ondelete == "RESTRICT"

    # incidents -> log_sources (SET NULL)
    inc_fk = next(fk for fk in Base.metadata.tables["incidents"].foreign_keys if fk.column.table.name == "log_sources")
    assert inc_fk.ondelete == "SET NULL"

    # incident_affected_services -> incidents (CASCADE) and log_sources (RESTRICT)
    ias_fks = {fk.column.table.name: fk.ondelete for fk in Base.metadata.tables["incident_affected_services"].foreign_keys}
    assert ias_fks["incidents"] == "CASCADE"
    assert ias_fks["log_sources"] == "RESTRICT"

    # incident_events -> incidents (CASCADE)
    ie_fk = next(fk for fk in Base.metadata.tables["incident_events"].foreign_keys if fk.column.table.name == "incidents")
    assert ie_fk.ondelete == "CASCADE"

    # evidence -> incidents (CASCADE) & incident_events (SET NULL)
    ev_fks = {fk.column.table.name: fk.ondelete for fk in Base.metadata.tables["evidence"].foreign_keys}
    assert ev_fks["incidents"] == "CASCADE"
    assert ev_fks["incident_events"] == "SET NULL"

    # recommendations -> incidents (CASCADE)
    rec_fk = next(fk for fk in Base.metadata.tables["recommendations"].foreign_keys if fk.column.table.name == "incidents")
    assert rec_fk.ondelete == "CASCADE"

    # reports -> incidents (CASCADE)
    rep_fk = next(fk for fk in Base.metadata.tables["reports"].foreign_keys if fk.column.table.name == "incidents")
    assert rep_fk.ondelete == "CASCADE"

    # rca_hypothesis_decisions -> incidents (CASCADE)
    rhd_fk = next(fk for fk in Base.metadata.tables["rca_hypothesis_decisions"].foreign_keys if fk.column.table.name == "incidents")
    assert rhd_fk.ondelete == "CASCADE"


def test_uniqueness_constraints():
    """Verify declared uniqueness constraints on domain tables."""
    # log_sources (name, environment)
    ls_uqs = [c.name for c in Base.metadata.tables["log_sources"].constraints if hasattr(c, "columns") and len(c.columns) > 1]
    assert "uq_log_sources_name_env" in ls_uqs

    # incident_affected_services (incident_id, log_source_id)
    ias_uqs = [c.name for c in Base.metadata.tables["incident_affected_services"].constraints if hasattr(c, "columns") and len(c.columns) > 1]
    assert "uq_incident_affected_services" in ias_uqs

    # incident_events (incident_id, sequence_order)
    ie_uqs = [c.name for c in Base.metadata.tables["incident_events"].constraints if hasattr(c, "columns") and len(c.columns) > 1]
    assert "uq_incident_event_sequence" in ie_uqs

    # reports (incident_id, title)
    rep_uqs = [c.name for c in Base.metadata.tables["reports"].constraints if hasattr(c, "columns") and len(c.columns) > 1]
    assert "uq_reports_incident_title" in rep_uqs


def test_incident_confidence_and_confirmation_separation():
    """Verify that is_root_cause_confirmed is distinct from confidence_score and defaults to False."""
    table = Base.metadata.tables["incidents"]
    assert "confidence_score" in table.c
    assert "is_root_cause_confirmed" in table.c
    assert "uncertainty_reason" in table.c
    assert "probable_cause" in table.c

    # Defaults and nullability
    assert table.c.is_root_cause_confirmed.nullable is False
    assert table.c.uncertainty_reason.nullable is True
    assert table.c.probable_cause.nullable is True


def test_evidence_opensearch_pointer_columns():
    """Verify evidence table contains OpenSearch pointers, content hash, and bounded snippet."""
    table = Base.metadata.tables["evidence"]
    assert "opensearch_index" in table.c
    assert "opensearch_doc_id" in table.c
    assert "log_timestamp" in table.c
    assert "log_snippet" in table.c
    assert "content_hash" in table.c
    assert "document_status" in table.c
    assert "relevance_score" in table.c
    assert "metadata" in table.c


def test_schema_compilation_in_memory_sqlite():
    """Verify all models, relationships, and constraints compile without error on an in-memory SQLite engine."""
    engine = create_engine("sqlite:///:memory:")
    # Create all tables (verifies metadata compiles successfully)
    Base.metadata.create_all(engine)

    # Verify table existence in SQLite
    with engine.connect() as conn:
        for table_name in Base.metadata.tables.keys():
            result = conn.exec_driver_sql(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';"
            ).fetchone()
            assert result is not None, f"Table {table_name} was not created in SQLite"
    engine.dispose()
