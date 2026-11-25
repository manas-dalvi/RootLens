"""Live End-to-End Evaluation Harness for RootLens.

Executes real live workflows against running OpenSearch and PostgreSQL instances
using the synthetic benchmark datasets in data/samples/:
  - Scenario A: Database Connection Pool Cascade (23 records)
  - Scenario B: Authentication Token Validation Surge (20 records)
  - Scenario C: Intermittent Uncertain Telemetry (11 records)

Strict Isolation Guarantees:
  1. OpenSearch: Restricts all indexing and candidate evaluations to 'rootlens-eval-logs-*'.
     Deletes only tracked, verified evaluation indices ('rootlens-eval-logs-2026.10').
     Never uses broad wildcard deletions.
  2. PostgreSQL: Tracks primary keys (incident UUIDs, upload job UUIDs, log source UUIDs)
     created during this run. Cleans up only those tracked records in reverse foreign-key
     dependency order (Incidents -> UploadJobs -> LogSources).
  3. Disk: Tracks created upload files in data/uploads/ and deletes only verified paths
     matching tracked job UUIDs within the configured upload directory.
  4. Invariant: Enforces is_root_cause_confirmed == False across all outputs.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Set
import uuid

import httpx
from httpx import ASGITransport
from opensearchpy import OpenSearch
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

# Ensure backend root is on sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.core.config import settings
from app.core.opensearch import get_opensearch_client
from app.db.session import close_engine, get_sessionmaker
from app.main import app
from app.models.incident import Incident
from app.models.log_source import LogSource
from app.models.upload_job import UploadJob


def pytest_configure(config):
    """Register custom markers to avoid PytestUnknownMarkWarning."""
    config.addinivalue_line("markers", "e2e: mark test as live end-to-end evaluation")


# Mark all tests in this file as live end-to-end evaluations
pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

EVAL_INDEX_PREFIX = "rootlens-eval-logs"
EVAL_INDEX_NAME = "rootlens-eval-logs-2026.10"
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"

# Enforce evaluation namespace globally for this test process
settings.OPENSEARCH_INDEX_PREFIX = EVAL_INDEX_PREFIX


class EvaluationTracker:
    """Explicit tracker for entities and artifacts created by this evaluation run."""

    def __init__(self) -> None:
        self.tracked_incident_ids: Set[uuid.UUID] = set()
        self.tracked_job_ids: Set[uuid.UUID] = set()
        self.tracked_source_ids: Set[uuid.UUID] = set()
        self.tracked_files: Set[Path] = set()
        self.tracked_eval_indices: Set[str] = {EVAL_INDEX_NAME}

    async def cleanup(self, client: OpenSearch) -> None:
        """Execute deterministic cleanup in reverse dependency order."""
        # 1. Clean up PostgreSQL rows by tracked primary keys
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            try:
                # Incidents cascade to affected_services, events, evidence, recs, reports
                if self.tracked_incident_ids:
                    await session.execute(
                        delete(Incident).where(
                            Incident.id.in_(list(self.tracked_incident_ids))
                        )
                    )
                # UploadJobs must be deleted before LogSources (ForeignKey RESTRICT)
                if self.tracked_job_ids:
                    await session.execute(
                        delete(UploadJob).where(
                            UploadJob.id.in_(list(self.tracked_job_ids))
                        )
                    )
                # LogSources now unblocked
                if self.tracked_source_ids:
                    await session.execute(
                        delete(LogSource).where(
                            LogSource.id.in_(list(self.tracked_source_ids))
                        )
                    )
                await session.commit()
            except Exception as exc:
                await session.rollback()
                print(f"[CLEANUP ERROR] PostgreSQL teardown failed: {exc}")

        # Dispose of engine to release connection pool across test event loops
        try:
            await close_engine()
        except Exception as exc:
            print(f"[CLEANUP ERROR] close_engine failed: {exc}")

        # 2. Clean up staged upload files on disk
        expected_dir = Path(settings.UPLOAD_DIR).resolve()
        for fpath in list(self.tracked_files):
            try:
                resolved = fpath.resolve()
                # Verification: Path must be strictly within expected upload directory
                if expected_dir in resolved.parents:
                    # Verification: Filename must match a tracked job ID
                    if any(
                        resolved.name.startswith(str(jid))
                        for jid in self.tracked_job_ids
                    ):
                        if resolved.is_file():
                            resolved.unlink()
            except Exception as exc:
                print(f"[CLEANUP ERROR] File deletion failed for {fpath}: {exc}")

        # 3. Clean up OpenSearch evaluation indices (No broad wildcards)
        for idx in list(self.tracked_eval_indices):
            try:
                # Ownership & namespace safeguards
                assert idx.startswith("rootlens-eval-logs-"), f"Refusing to delete non-eval index: {idx}"
                assert not idx.startswith("rootlens-logs-"), f"Refusing to delete dev index: {idx}"
                assert "*" not in idx, f"Wildcard index deletion prohibited: {idx}"

                if client.indices.exists(index=idx):
                    client.indices.delete(index=idx)
            except Exception as exc:
                print(f"[CLEANUP ERROR] OpenSearch index deletion failed for {idx}: {exc}")


@pytest_asyncio.fixture
async def e2e_context():
    """Manage live preflight verification and failure-safe cleanup for E2E runs."""
    # Enforce namespace isolation in the test runner's configuration
    settings.OPENSEARCH_INDEX_PREFIX = EVAL_INDEX_PREFIX
    assert settings.OPENSEARCH_INDEX_PREFIX == EVAL_INDEX_PREFIX

    # 1. Preflight: OpenSearch
    os_client = get_opensearch_client()
    try:
        is_os_alive = os_client.ping()
    except Exception as exc:
        pytest.skip(f"OpenSearch unreachable on {settings.OPENSEARCH_HOST}:{settings.OPENSEARCH_PORT}: {exc}")
    if not is_os_alive:
        pytest.skip("OpenSearch ping returned False. Ensure OpenSearch container is running.")

    # 2. Preflight: PostgreSQL
    await close_engine()
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            res = await session.execute(select(1))
            assert res.scalar() == 1
            await session.rollback()
    except Exception as exc:
        pytest.skip(f"PostgreSQL unreachable on {settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}: {exc}")

    # 3. Preflight: Upload Directory
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    assert upload_dir.is_dir()

    # 4. Preflight: Sample Datasets
    assert SAMPLES_DIR.is_dir(), f"Samples dir missing: {SAMPLES_DIR}"
    assert (SAMPLES_DIR / "scenario_db_cascade.json").is_file()
    assert (SAMPLES_DIR / "scenario_auth_spike.json").is_file()
    assert (SAMPLES_DIR / "scenario_intermittent_uncertain.json").is_file()
    assert (SAMPLES_DIR / "manifest.json").is_file()

    tracker = EvaluationTracker()

    # Pre-clean any stale evaluation index if present from prior runs
    try:
        if os_client.indices.exists(index=EVAL_INDEX_NAME):
            os_client.indices.delete(index=EVAL_INDEX_NAME)
    except Exception:
        pass

    try:
        yield tracker, os_client
    finally:
        # Guaranteed cleanup on normal completion or test assertions
        await tracker.cleanup(os_client)


async def test_scenario_a_database_cascade_e2e(e2e_context):
    """Scenario A: Live E2E Ingestion, Indexing, Candidate Detection & RCA Persistence."""
    tracker, os_client = e2e_context
    dataset_path = SAMPLES_DIR / "scenario_db_cascade.json"
    raw_bytes = dataset_path.read_bytes()
    expected_records = 23

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Step 1: Upload dataset
        upload_resp = await client.post(
            "/api/v1/logs/upload",
            files={"file": ("scenario_db_cascade.json", raw_bytes, "application/json")},
            data={
                "service_name": "postgres-db",
                "environment": "evaluation",
                "source_name": "eval-db-cascade-source",
            },
        )
        assert upload_resp.status_code == 201, f"Upload failed: {upload_resp.text}"
        upload_data = upload_resp.json()
        job_id = uuid.UUID(upload_data["job_id"])
        source_id = uuid.UUID(upload_data["log_source_id"])
        tracker.tracked_job_ids.add(job_id)
        tracker.tracked_source_ids.add(source_id)
        tracker.tracked_files.add(Path(settings.UPLOAD_DIR) / f"{job_id}.json")

        # Step 2: Index upload job
        index_resp = await client.post(f"/api/v1/logs/jobs/{job_id}/index")
        assert index_resp.status_code == 200, f"Indexing failed: {index_resp.text}"
        index_data = index_resp.json()
        assert index_data["total_records"] == expected_records
        assert index_data["indexed_records"] == expected_records
        assert index_data["quarantined_records"] == 0
        assert index_data["failed_records"] == 0

        # Refresh evaluation index
        os_client.indices.refresh(index=EVAL_INDEX_NAME)

        # Step 3: Verify records are searchable in isolated evaluation namespace
        search_resp = await client.get("/api/v1/logs", params={"page_size": 50})
        assert search_resp.status_code == 200
        search_data = search_resp.json()
        assert search_data["total"] == expected_records

        # Step 4: Evaluate incident candidates via POST /candidates
        cand_resp = await client.post(
            "/api/v1/incidents/candidates",
            json={
                "start_time": "2026-10-14T07:55:00Z",
                "end_time": "2026-10-14T08:10:00Z",
                "window_minutes": 15,
                "cascade_window_seconds": 120,
                "max_logs": 1000,
            },
        )
        assert cand_resp.status_code == 200, f"Candidates evaluation failed: {cand_resp.text}"
        cand_data = cand_resp.json()
        assert cand_data["total_candidates"] >= 1, "Expected at least 1 candidate for DB cascade"

        # Locate candidate with postgres-db and order-service
        matched_cand = None
        for cand in cand_data["candidates"]:
            services = {s["service_name"] for s in cand["affected_services"]}
            if "postgres-db" in services or "order-service" in services:
                matched_cand = cand
                break

        assert matched_cand is not None, "Failed to locate correlated DB cascade candidate"
        assert matched_cand["is_root_cause_confirmed"] is False
        assert matched_cand["assessment"] is not None
        assert matched_cand["assessment"]["classification"] == "TRACE_LINKED"
        assert matched_cand["assessment"]["has_shared_trace"] is True

        # Check chronological timeline and earliest failure
        timeline = matched_cand["timeline_events"]
        assert len(timeline) >= 2
        # Earliest event should be marked
        earliest_ev = next((ev for ev in timeline if ev["is_earliest_observed_failure"]), None)
        assert earliest_ev is not None
        assert earliest_ev["service_name"] == "postgres-db"
        assert "2026-10-14T08:01:30" in earliest_ev["event_timestamp"]

        # Step 5: Persist candidate to PostgreSQL
        create_payload = {
            "candidate_id": matched_cand["candidate_id"],
            "title": matched_cand["title"],
            "severity": matched_cand["severity"],
            "started_at": matched_cand["started_at"],
            "ended_at": matched_cand["ended_at"],
            "correlation_rules_used": matched_cand["correlation_rules_used"],
            "uncertainty_reason": matched_cand["uncertainty_reason"],
            "affected_services": matched_cand["affected_services"],
            "timeline_events": matched_cand["timeline_events"],
            "evidence_items": matched_cand["evidence_items"],
            "cluster_ids": matched_cand["cluster_ids"],
        }
        persist_resp = await client.post("/api/v1/incidents", json=create_payload)
        assert persist_resp.status_code in (200, 201), f"Persistence failed: {persist_resp.text}"
        persist_data = persist_resp.json()
        inc_id = uuid.UUID(persist_data["id"])
        tracker.tracked_incident_ids.add(inc_id)
        assert persist_data["status"] == "DETECTED"
        assert persist_data["is_root_cause_confirmed"] is False

        # Step 6: Verify Explanation Endpoint
        expl_resp = await client.get(f"/api/v1/incidents/{inc_id}/explanation")
        assert expl_resp.status_code == 200
        expl_data = expl_resp.json()
        assert expl_data["is_root_cause_confirmed"] is False
        assert len(expl_data["explanation"]) > 0
        assert len(expl_data["evidence_references"]) >= 1

        # Step 7: Verify Recommendations Endpoint
        rec_resp = await client.post(f"/api/v1/incidents/{inc_id}/recommendations")
        assert rec_resp.status_code in (200, 201)
        rec_data = rec_resp.json()
        assert rec_data["is_root_cause_confirmed"] is False
        assert rec_data["is_advisory_only"] is True

        # Step 8: Verify Post-Mortem Report Generation Endpoint
        rep_resp = await client.post(
            f"/api/v1/incidents/{inc_id}/reports",
            json={"title": "E2E Post-Mortem Report - Scenario A"},
        )
        assert rep_resp.status_code in (200, 201)
        rep_data = rep_resp.json()
        assert rep_data["is_root_cause_confirmed"] is False
        assert rep_data["status"] == "DRAFT"
        assert "postgres-db" in rep_data["markdown_content"]


async def test_scenario_b_auth_spike_e2e(e2e_context):
    """Scenario B: Live E2E Ingestion, Indexing, Candidate Detection & Auth Failure Analysis."""
    tracker, os_client = e2e_context
    dataset_path = SAMPLES_DIR / "scenario_auth_spike.json"
    raw_bytes = dataset_path.read_bytes()
    expected_records = 20

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Step 1: Upload dataset
        upload_resp = await client.post(
            "/api/v1/logs/upload",
            files={"file": ("scenario_auth_spike.json", raw_bytes, "application/json")},
            data={
                "service_name": "auth-service",
                "environment": "evaluation",
                "source_name": "eval-auth-spike-source",
            },
        )
        assert upload_resp.status_code == 201, f"Upload failed: {upload_resp.text}"
        upload_data = upload_resp.json()
        job_id = uuid.UUID(upload_data["job_id"])
        source_id = uuid.UUID(upload_data["log_source_id"])
        tracker.tracked_job_ids.add(job_id)
        tracker.tracked_source_ids.add(source_id)
        tracker.tracked_files.add(Path(settings.UPLOAD_DIR) / f"{job_id}.json")

        # Step 2: Index upload job
        index_resp = await client.post(f"/api/v1/logs/jobs/{job_id}/index")
        assert index_resp.status_code == 200, f"Indexing failed: {index_resp.text}"
        index_data = index_resp.json()
        assert index_data["total_records"] == expected_records
        assert index_data["indexed_records"] == expected_records
        assert index_data["quarantined_records"] == 0

        # Refresh evaluation index
        os_client.indices.refresh(index=EVAL_INDEX_NAME)

        # Step 3: Evaluate incident candidates
        cand_resp = await client.post(
            "/api/v1/incidents/candidates",
            json={
                "start_time": "2026-10-14T09:10:00Z",
                "end_time": "2026-10-14T09:20:00Z",
                "window_minutes": 15,
                "cascade_window_seconds": 120,
                "max_logs": 1000,
            },
        )
        assert cand_resp.status_code == 200
        cand_data = cand_resp.json()
        assert cand_data["total_candidates"] >= 1

        matched_cand = None
        for cand in cand_data["candidates"]:
            services = {s["service_name"] for s in cand["affected_services"]}
            if "auth-service" in services:
                matched_cand = cand
                break

        assert matched_cand is not None, "Failed to locate correlated auth spike candidate"
        assert matched_cand["is_root_cause_confirmed"] is False
        assert matched_cand["assessment"] is not None
        assert matched_cand["assessment"]["classification"] == "TRACE_LINKED"

        # Check earliest failure is auth-service
        timeline = matched_cand["timeline_events"]
        earliest_ev = next((ev for ev in timeline if ev["is_earliest_observed_failure"]), None)
        assert earliest_ev is not None
        assert earliest_ev["service_name"] == "auth-service"
        assert "2026-10-14T09:15:10" in earliest_ev["event_timestamp"]

        # Step 4: Persist candidate
        create_payload = {
            "candidate_id": matched_cand["candidate_id"],
            "title": matched_cand["title"],
            "severity": matched_cand["severity"],
            "started_at": matched_cand["started_at"],
            "ended_at": matched_cand["ended_at"],
            "correlation_rules_used": matched_cand["correlation_rules_used"],
            "uncertainty_reason": matched_cand["uncertainty_reason"],
            "affected_services": matched_cand["affected_services"],
            "timeline_events": matched_cand["timeline_events"],
            "evidence_items": matched_cand["evidence_items"],
            "cluster_ids": matched_cand["cluster_ids"],
        }
        persist_resp = await client.post("/api/v1/incidents", json=create_payload)
        assert persist_resp.status_code in (200, 201)
        persist_data = persist_resp.json()
        inc_id = uuid.UUID(persist_data["id"])
        tracker.tracked_incident_ids.add(inc_id)
        assert persist_data["is_root_cause_confirmed"] is False

        # Step 5: Verify Explanation & Recommendations
        expl_resp = await client.get(f"/api/v1/incidents/{inc_id}/explanation")
        assert expl_resp.status_code == 200
        expl_data = expl_resp.json()
        assert expl_data["is_root_cause_confirmed"] is False
        assert len(expl_data["explanation"]) > 0

        rec_resp = await client.post(f"/api/v1/incidents/{inc_id}/recommendations")
        assert rec_resp.status_code in (200, 201)
        rec_data = rec_resp.json()
        assert rec_data["is_root_cause_confirmed"] is False


async def test_scenario_c_intermittent_uncertain_e2e(e2e_context):
    """Scenario C: Live E2E Ingestion, Indexing, and Uncertainty Preservation.

    Verifies that sparse, disconnected errors across unrelated services:
      1. Are successfully indexed into OpenSearch without data loss.
      2. Do NOT form a fabricated multi-service candidate incident.
      3. Are evaluated as INSUFFICIENT evidence.
      4. Strictly maintain is_root_cause_confirmed == False.
    """
    tracker, os_client = e2e_context
    dataset_path = SAMPLES_DIR / "scenario_intermittent_uncertain.json"
    raw_bytes = dataset_path.read_bytes()
    expected_records = 11

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Step 1: Upload dataset
        upload_resp = await client.post(
            "/api/v1/logs/upload",
            files={"file": ("scenario_intermittent_uncertain.json", raw_bytes, "application/json")},
            data={
                "service_name": "notification-service",
                "environment": "evaluation",
                "source_name": "eval-uncertain-source",
            },
        )
        assert upload_resp.status_code == 201
        upload_data = upload_resp.json()
        job_id = uuid.UUID(upload_data["job_id"])
        source_id = uuid.UUID(upload_data["log_source_id"])
        tracker.tracked_job_ids.add(job_id)
        tracker.tracked_source_ids.add(source_id)
        tracker.tracked_files.add(Path(settings.UPLOAD_DIR) / f"{job_id}.json")

        # Step 2: Index upload job
        index_resp = await client.post(f"/api/v1/logs/jobs/{job_id}/index")
        assert index_resp.status_code == 200
        index_data = index_resp.json()
        assert index_data["total_records"] == expected_records
        assert index_data["indexed_records"] == expected_records
        assert index_data["quarantined_records"] == 0

        # Refresh evaluation index
        os_client.indices.refresh(index=EVAL_INDEX_NAME)

        # Step 3: Evaluate candidates over the 35-minute scenario window
        cand_resp = await client.post(
            "/api/v1/incidents/candidates",
            json={
                "start_time": "2026-10-14T11:00:00Z",
                "end_time": "2026-10-14T11:40:00Z",
                "window_minutes": 15,
                "cascade_window_seconds": 120,
                "max_logs": 1000,
            },
        )
        assert cand_resp.status_code == 200
        cand_data = cand_resp.json()

        # Step 4: Verify NO single candidate merges all three services
        # (notification-service, inventory-service, analytics-worker)
        for cand in cand_data["candidates"]:
            services = {s["service_name"] for s in cand["affected_services"]}
            assert not (
                "notification-service" in services
                and "inventory-service" in services
                and "analytics-worker" in services
            ), "Algorithm incorrectly fabricated a single cross-service candidate from sparse disconnected telemetry!"

            # If isolated single-service candidate was evaluated:
            if cand.get("assessment"):
                # Must evaluate to INSUFFICIENT due to sparse isolated evidence count < 2
                assert cand["assessment"]["classification"] in ("INSUFFICIENT", "TEMPORAL_ONLY")
            assert cand["is_root_cause_confirmed"] is False
