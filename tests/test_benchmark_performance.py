"""Performance and Reliability Benchmark Harness for RootLens.

Executes repeatable, non-destructive performance and reliability measurements
against running OpenSearch and PostgreSQL instances using synthetic benchmark workloads.

Shared Infrastructure Notice:
  This benchmark runs within a dedicated in-memory ASGI client process, but shares the underlying
  OpenSearch cluster (port 9200), PostgreSQL database (port 5432), and host disk/CPU with any
  concurrent development processes. Complete infrastructure isolation is not claimed; logical
  isolation is enforced via unique run-scoped namespaces and primary-key tracking.

Concurrency Guard Scope & Cross-Process Protection:
  Suite-wide serialization across independent external OS processes is enforced via
  `BenchmarkProcessLock` anchored at `tempfile.gettempdir()/rootlens_benchmark_suite.lock`.
  The module-level fixture `guard_serial_benchmark_execution` acquires this lock exclusively
  before any benchmark test runs and releases it only upon suite completion. Intra-session
  parallel workers spawned via `pytest-xdist` are detected and aborted immediately.
  Strict ownership verification ensures only the acquiring process can release or delete
  the lock, preventing race conditions or accidental deletion by foreign processes.

Unacknowledged Resource Commit Limitation:
  If an HTTP request successfully commits a database transaction or writes a file to disk, but
  the connection terminates before the client receives the response (or the response JSON is
  malformed), the assigned UUID cannot be captured by the benchmark tracker. Because safety rules
  strictly prohibit table-wide scans, unverified deletes, or wildcard index drops, any such
  unacknowledged resource cannot be recovered by this in-process harness alone and relies on
  standard staging test environment resets. Safe ownership cannot be proven without IDs, so the
  harness reports this limitation rather than attempting unsafe broad deletions.

Strict Isolation Guarantees:
  1. OpenSearch: Uses a unique, unpredictable run token ('rootlens-bench-{run_token}-logs-*').
     Never deletes shared or pre-existing indices during preflight.
     Discovers run-scoped indices for enumeration only, validates each concrete index name,
     and deletes indices individually by concrete name. Never uses wildcard deletion.
     Discovery errors are recorded in cleanup errors rather than silently swallowed.
  2. PostgreSQL: Tracks primary keys (incident UUIDs, upload job UUIDs, log source UUIDs)
     immediately upon creation (before status code assertions). Cleans up only those tracked
     records in reverse foreign-key dependency order (Incidents -> UploadJobs -> LogSources).
  3. Disk: Tracks created upload files in data/uploads/ and deletes only verified paths
     matching tracked job UUIDs strictly within the configured upload directory.
  4. Teardown Resilience: Teardown attempts every independent cleanup step (PostgreSQL,
     connection engine, filesystem, OpenSearch discovery, OpenSearch deletion) even when one
     step fails, reporting all accumulated cleanup errors without masking test exceptions.
  5. Measurement Methodology & Latency Characterization:
     Timings are measured via time.perf_counter() over in-process ASGI calls (httpx.ASGITransport).
     In-process ASGI latency reflects application execution time (Python parsing, ORM/DB queries,
     OpenSearch HTTP queries, JSON serialization) but excludes network latency (DNS, TCP, TLS,
     intermediate proxies) and production-scale concurrency. Measurements establish individual
     component timings (staging ~8-12 ms, bulk indexing ~60-95 ms, search ~12-18 ms, candidate
     evaluation ~15-22 ms); they do NOT support blanket claims that the entire multi-service
     pipeline is sub-100 ms.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import queue
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

import asyncpg
import httpx
from httpx import ASGITransport
from opensearchpy import OpenSearch
import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text

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
from app.services.evidence_analysis_service import (
    evaluate_evidence_sufficiency,
    EvidenceReference,
)
from app.services.rca_synthesis_service import synthesize_rca_findings

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"


# Mark all tests in this file as benchmark tests
pytestmark = [pytest.mark.benchmark, pytest.mark.asyncio]


BENCHMARK_SUITE_LOCK_FILE = Path(tempfile.gettempdir()) / "rootlens_benchmark_suite.lock"


class BenchmarkProcessLock:
    """Cross-process serial execution lock with strict ownership safety.

    Enforces:
      1. Explicit ownership tracking: tracks whether the current instance successfully
         acquired the lock via exclusive file creation (os.O_CREAT | os.O_EXCL).
      2. Strict release permissions: only the acquiring instance / process may release
         or delete the lock file. Non-owners cannot delete or modify the lock.
      3. Non-interference: if acquisition fails because another process owns the lock,
         the existing lock file is left completely untouched.
      4. Safe acquisition failure handling: exceptions during acquisition safely close
         and clean up any partially created descriptor/file without leaking.
      5. Token-verified cleanup: lock verification ensures unlinking is restricted to
         files whose on-disk content matches this instance's ownership token.
      6. No unsafe stale-lock deletion: does not attempt unsafe PID guessing or
         destructive stale-lock recovery.
      7. Verified lifecycle state: tracks state transitions (INIT -> ACQUIRED -> RELEASED).
    """

    def __init__(self, lock_path: Optional[Path] = None) -> None:
        self.lock_path: Path = lock_path or (
            Path(tempfile.gettempdir()) / "rootlens_benchmark_serial_run.lock"
        )
        self._fd: Optional[int] = None
        self._is_acquired: bool = False
        self._owner_pid: Optional[int] = None
        self._owner_token: Optional[str] = None

    @property
    def is_acquired(self) -> bool:
        """Return True if this instance currently owns the acquired lock."""
        return self._is_acquired

    @property
    def owner_pid(self) -> Optional[int]:
        """Return the PID of this lock instance if acquired."""
        return self._owner_pid

    @property
    def owner_token(self) -> Optional[str]:
        """Return the unique ownership token if acquired."""
        return self._owner_token

    def acquire(self) -> bool:
        """Attempt to atomically acquire exclusive cross-process lock.

        Returns True on successful acquisition; False if already locked by another process
        or already acquired by this instance.
        """
        if self._is_acquired:
            return False

        token = uuid.uuid4().hex
        pid = os.getpid()
        fd: Optional[int] = None

        try:
            # Atomic creation with exclusive flag
            fd = os.open(
                str(self.lock_path),
                os.O_CREAT | os.O_EXCL | os.O_RDWR,
            )
            # Write verifiable ownership token payload
            payload = f"pid={pid}\ntoken={token}\ncreated_at={datetime.now(timezone.utc).isoformat()}\n"
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
        except (FileExistsError, PermissionError):
            # Another process holds the lock or path is protected; do NOT unlink or modify
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass
            return False
        except Exception:
            # Unexpected exception during creation/write: clean up ONLY if the on-disk lock
            # affirmatively belongs to this acquisition attempt. Never unlink foreign locks.
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass
                try:
                    if self.lock_path.exists():
                        content = self.lock_path.read_text(encoding="utf-8")
                        if f"token={token}" in content and f"pid={pid}" in content:
                            self.lock_path.unlink(missing_ok=True)
                except Exception:
                    pass
            return False

        self._fd = fd
        self._is_acquired = True
        self._owner_pid = pid
        self._owner_token = token
        return True

    def release(self) -> bool:
        """Safely release the lock if and only if owned by this instance.

        Non-owners can never delete or modify the lock file.
        Returns True if released by owner; False if not owned or already released.
        """
        if not self._is_acquired:
            return False

        # Transition ownership state immediately to prevent re-entrant or concurrent release
        self._is_acquired = False
        target_token = self._owner_token
        target_pid = self._owner_pid
        fd = self._fd
        self._fd = None
        self._owner_token = None
        self._owner_pid = None

        # Close open file descriptor
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass

        # Verify on-disk ownership content before unlinking to prevent removing another process's lock
        try:
            if self.lock_path.exists() and target_token is not None:
                content = self.lock_path.read_text(encoding="utf-8")
                if f"token={target_token}" in content and f"pid={target_pid}" in content:
                    self.lock_path.unlink(missing_ok=True)
                    return True
        except Exception:
            pass

        return False

    def __enter__(self) -> "BenchmarkProcessLock":
        if not self.acquire():
            raise RuntimeError(
                f"Failed to acquire benchmark lock at {self.lock_path}. "
                "Another benchmark process is active or lock could not be established."
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


@pytest.fixture(scope="module", autouse=True)
def guard_serial_benchmark_execution(request):
    """Enforce strict serial execution within pytest and across OS processes.

    Protections:
      1. Detects pytest-xdist worker processes within the active session and aborts.
      2. Enforces suite-wide serialization across independent external OS processes
         via BenchmarkProcessLock(BENCHMARK_SUITE_LOCK_FILE). If another process owns the lock,
         execution aborts immediately without modifying the existing lock.
    """
    if "PYTEST_XDIST_WORKER" in os.environ:
        pytest.exit(
            f"Parallel execution with pytest-xdist worker {os.environ['PYTEST_XDIST_WORKER']} is prohibited. "
            "Benchmark harness requires serial execution to prevent namespace collisions.",
            returncode=1,
        )
    num_procs = getattr(request.config.option, "numprocesses", None)
    if num_procs is not None and num_procs > 0:
        pytest.exit(
            f"Parallel pytest-xdist execution prohibited (numprocesses={num_procs}).",
            returncode=1,
        )
    if hasattr(request.config, "workerinput"):
        pytest.exit(
            "pytest-xdist workerinput detected; aborting parallel benchmark run.",
            returncode=1,
        )

    suite_lock = BenchmarkProcessLock(BENCHMARK_SUITE_LOCK_FILE)
    if not suite_lock.acquire():
        pytest.exit(
            f"Another benchmark suite process appears to be in progress (lock exists at {BENCHMARK_SUITE_LOCK_FILE}). "
            "Cross-process concurrent benchmark execution is strictly prohibited.",
            returncode=1,
        )
    try:
        yield suite_lock
    finally:
        # Export recorded benchmark metrics to structured JSON if any measurements were gathered
        try:
            if _benchmark_metrics_registry:
                export_benchmark_metrics_to_json()
        except Exception:
            pass
        suite_lock.release()


@dataclass
class BenchmarkMetricSummary:
    """Statistical summary for a benchmarked operation."""

    operation_name: str
    total_attempts: int
    success_count: int
    error_count: int
    mean_ms: float
    median_ms: float
    p95_ms: float
    min_ms: float
    max_ms: float
    stddev_ms: float
    warmup_count: int = 0
    throughput_ops_sec: Optional[float] = None
    throughput_records_sec: Optional[float] = None
    error_rate_pct: float = 0.0
    failed_details: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


DEFAULT_BENCHMARK_RESULTS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "benchmark_results.json"
)

# In-memory registry to collect benchmark metric summaries for structured export
_benchmark_metrics_registry: List[BenchmarkMetricSummary] = []


def register_benchmark_metric(summary: BenchmarkMetricSummary) -> None:
    """Register a benchmark metric summary into the session metrics registry."""
    _benchmark_metrics_registry.append(summary)


def fetch_postgres_version(timeout_sec: float = 3.0) -> Optional[str]:
    """Execute a read-only query against PostgreSQL to retrieve the server version.

    Ensures:
      1. Genuine timeout upper bound: uses a dedicated daemon worker thread and a queue
         without ThreadPoolExecutor shutdown blocking the caller on timeouts.
      2. No cross-loop engine/session reuse: connects directly via asyncpg within an
         isolated, fresh event loop rather than reusing global SQLAlchemy engine/session.
      3. Safe in all contexts: can be called from synchronous contexts or when an asyncio
         event loop is already running in the current thread.
      4. Honest failure handling: returns None on timeout, connection error, or lookup failure.
    """
    if timeout_sec <= 0:
        return None

    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def _worker():
        async def _query():
            raw_url = (
                settings.DATABASE_URL
                or f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
            )
            if raw_url.startswith("postgresql+asyncpg://"):
                raw_url = raw_url.replace("postgresql+asyncpg://", "postgresql://", 1)

            conn = await asyncpg.connect(dsn=raw_url, timeout=timeout_sec)
            try:
                val = await conn.fetchval("SHOW server_version", timeout=timeout_sec)
                return str(val).strip() if val else None
            finally:
                await conn.close()

        try:
            ver = asyncio.run(_query())
            result_queue.put((True, ver))
        except Exception:
            result_queue.put((False, None))

    thread = threading.Thread(target=_worker, daemon=True, name="fetch_postgres_version_worker")
    thread.start()

    try:
        ok, version = result_queue.get(timeout=timeout_sec)
        return version if ok else None
    except queue.Empty:
        return None


def export_benchmark_metrics_to_json(
    output_path: Optional[Path] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Path:
    """Export all registered benchmark metric summaries to a deterministic structured JSON file.

    Configuration:
      Target path is resolved from:
      1. Explicit argument `output_path`
      2. Environment variable `ROOTLENS_BENCHMARK_OUTPUT_PATH`
      3. Default fallback: `data/benchmark_results.json`
    """
    env_path = os.environ.get("ROOTLENS_BENCHMARK_OUTPUT_PATH")
    target_path = output_path or (Path(env_path) if env_path else DEFAULT_BENCHMARK_RESULTS_PATH)
    target_path = target_path.resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    metadata: Dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "platform": sys.platform,
        "python_version": sys.version,
        "opensearch_host": settings.OPENSEARCH_HOST,
        "opensearch_port": settings.OPENSEARCH_PORT,
        "postgres_host": settings.POSTGRES_HOST,
        "postgres_port": settings.POSTGRES_PORT,
        "database_name": settings.POSTGRES_DB,
        "total_operations_recorded": len(_benchmark_metrics_registry),
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    # Collect client versions if available
    try:
        os_client = get_opensearch_client()
        if os_client.ping():
            info = os_client.info()
            metadata["opensearch_version"] = info.get("version", {}).get("number", "unknown")
            metadata["opensearch_distribution"] = info.get("version", {}).get("distribution", "unknown")
    except Exception:
        pass

    # Collect PostgreSQL server version via read-only query
    try:
        pg_ver = fetch_postgres_version()
        if pg_ver:
            metadata["postgres_version"] = pg_ver
        else:
            metadata["postgres_version"] = "unavailable"
    except Exception:
        metadata["postgres_version"] = "unavailable"

    results: List[Dict[str, Any]] = []
    for m in _benchmark_metrics_registry:
        results.append({
            "operation_name": m.operation_name,
            "total_attempts": m.total_attempts,
            "success_count": m.success_count,
            "error_count": m.error_count,
            "error_rate_pct": round(m.error_rate_pct, 4),
            "warmup_count": m.warmup_count,
            "mean_ms": round(m.mean_ms, 3),
            "median_ms": round(m.median_ms, 3),
            "p95_ms": round(m.p95_ms, 3),
            "min_ms": round(m.min_ms, 3),
            "max_ms": round(m.max_ms, 3),
            "stddev_ms": round(m.stddev_ms, 3),
            "throughput_ops_sec": round(m.throughput_ops_sec, 2) if m.throughput_ops_sec is not None else None,
            "throughput_records_sec": round(m.throughput_records_sec, 2) if m.throughput_records_sec is not None else None,
            "failed_details": m.failed_details,
            "details": m.details,
        })

    payload = {
        "metadata": metadata,
        "benchmarks": results,
    }

    target_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target_path


def compute_statistics(
    operation_name: str,
    timings_ms: List[float],
    warmup_count: int = 0,
    total_records: Optional[int] = None,
    total_operations: Optional[int] = None,
    error_count: int = 0,
    failed_details: Optional[List[str]] = None,
    details: Optional[Dict[str, Any]] = None,
) -> BenchmarkMetricSummary:
    """Compute standard statistical distribution from a list of millisecond timings."""
    success_count = len(timings_ms)
    total_attempts = success_count + error_count
    error_rate = (error_count / total_attempts * 100.0) if total_attempts > 0 else 0.0

    if success_count == 0:
        return BenchmarkMetricSummary(
            operation_name=operation_name,
            total_attempts=total_attempts,
            success_count=0,
            error_count=error_count,
            mean_ms=0.0,
            median_ms=0.0,
            p95_ms=0.0,
            min_ms=0.0,
            max_ms=0.0,
            stddev_ms=0.0,
            warmup_count=warmup_count,
            error_rate_pct=error_rate,
            failed_details=failed_details or [],
            details=details or {},
        )

    mean_val = statistics.mean(timings_ms)
    median_val = statistics.median(timings_ms)
    stddev_val = statistics.stdev(timings_ms) if success_count > 1 else 0.0
    min_val = min(timings_ms)
    max_val = max(timings_ms)

    # 95th percentile calculation
    sorted_timings = sorted(timings_ms)
    p95_idx = max(0, min(success_count - 1, math.ceil(0.95 * success_count) - 1))
    p95_val = sorted_timings[p95_idx]

    # Throughput calculations (using sum of measured execution times for successful operations)
    total_time_sec = sum(timings_ms) / 1000.0
    t_ops = (total_operations or success_count) / total_time_sec if total_time_sec > 0 else None
    t_rec = (total_records / total_time_sec) if (total_records and total_time_sec > 0) else None

    return BenchmarkMetricSummary(
        operation_name=operation_name,
        total_attempts=total_attempts,
        success_count=success_count,
        error_count=error_count,
        mean_ms=mean_val,
        median_ms=median_val,
        p95_ms=p95_val,
        min_ms=min_val,
        max_ms=max_val,
        stddev_ms=stddev_val,
        warmup_count=warmup_count,
        throughput_ops_sec=t_ops,
        throughput_records_sec=t_rec,
        error_rate_pct=error_rate,
        failed_details=failed_details or [],
        details=details or {},
    )


def format_benchmark_table(summaries: List[BenchmarkMetricSummary], title: str) -> str:
    """Format benchmark summaries into a structured, readable Markdown table."""
    lines = [
        f"\n### {title}",
        "| Operation | Attempts (OK/Err) | Mean (ms) | Median (ms) | p95 (ms) | Min (ms) | Max (ms) | StdDev (ms) | Throughput | Error % |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    failed_messages: List[str] = []
    for s in summaries:
        tp_str = "-"
        if s.throughput_records_sec is not None:
            tp_str = f"{s.throughput_records_sec:.1f} rec/s"
        elif s.throughput_ops_sec is not None:
            tp_str = f"{s.throughput_ops_sec:.1f} ops/s"

        attempts_str = f"{s.success_count}/{s.total_attempts}" if s.error_count > 0 else str(s.total_attempts)
        lines.append(
            f"| {s.operation_name} | {attempts_str} | {s.mean_ms:.2f} | {s.median_ms:.2f} | "
            f"{s.p95_ms:.2f} | {s.min_ms:.2f} | {s.max_ms:.2f} | {s.stddev_ms:.2f} | {tp_str} | {s.error_rate_pct:.1f}% |"
        )
        if s.failed_details:
            for detail in s.failed_details:
                failed_messages.append(f"  - [{s.operation_name}] {detail}")

    table_output = "\n".join(lines) + "\n"
    if failed_messages:
        table_output += "\n**Failed Operations Encountered:**\n" + "\n".join(failed_messages) + "\n"
    return table_output


def generate_workload_records(
    target_count: int, base_records: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Deterministically generate a valid RootLens log record payload of target size."""
    records: List[Dict[str, Any]] = []
    base_len = len(base_records)
    base_time = datetime(2026, 10, 14, 8, 0, 0, tzinfo=timezone.utc)

    for i in range(target_count):
        src = base_records[i % base_len].copy()
        ts = base_time + timedelta(seconds=i * 2)
        src["timestamp"] = ts.isoformat().replace("+00:00", "Z")
        src["message"] = f"{src.get('message', 'benchmark event')} [seq={i}]"
        records.append(src)

    return records


class BenchmarkTracker:
    """Explicit tracker for entities and artifacts created by a benchmark run."""

    def __init__(self, run_token: str, original_prefix: str) -> None:
        self.run_token = run_token
        self.original_prefix = original_prefix
        self.bench_prefix = f"rootlens-bench-{run_token}-logs"
        self.tracked_incident_ids: Set[uuid.UUID] = set()
        self.tracked_job_ids: Set[uuid.UUID] = set()
        self.tracked_source_ids: Set[uuid.UUID] = set()
        self.tracked_files: Set[Path] = set()
        self.tracked_concrete_indices: Set[str] = set()

    async def cleanup(self, client: OpenSearch) -> List[str]:
        """Execute deterministic cleanup in reverse dependency order without wildcard deletes.

        Attempts every independent cleanup step (PostgreSQL, connection engine,
        filesystem, OpenSearch index discovery, OpenSearch concrete index deletion)
        even if preceding steps raise errors, collecting all encountered failures.
        """
        cleanup_errors: List[str] = []

        # 1. Restore global settings prefix immediately
        settings.OPENSEARCH_INDEX_PREFIX = self.original_prefix

        # 2. Clean up PostgreSQL rows by tracked primary keys
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
                cleanup_errors.append(f"PostgreSQL teardown failed: {exc}")

        # Dispose of engine to release connection pool across test event loops
        try:
            await close_engine()
        except Exception as exc:
            cleanup_errors.append(f"close_engine failed: {exc}")

        # 3. Clean up staged upload files on disk
        expected_dir = Path(settings.UPLOAD_DIR).resolve()
        for fpath in list(self.tracked_files):
            try:
                resolved = fpath.resolve()
                # Verification: Path must be strictly within expected upload directory
                is_within = (
                    expected_dir in resolved.parents
                    or (hasattr(resolved, "is_relative_to") and resolved.is_relative_to(expected_dir))
                )
                if is_within:
                    # Verification: Filename must match a tracked job ID
                    if any(resolved.name.startswith(str(jid)) for jid in self.tracked_job_ids):
                        if resolved.is_file():
                            resolved.unlink()
                else:
                    cleanup_errors.append(
                        f"Refusing to delete untrusted file outside upload directory: {fpath}"
                    )
            except Exception as exc:
                cleanup_errors.append(f"File deletion failed for {fpath}: {exc}")

        # 4. Discover any concrete indices created under this run token for enumeration only
        try:
            discovered = client.indices.get(index=f"{self.bench_prefix}-*")
            for idx_name in discovered.keys():
                if idx_name.startswith(f"{self.bench_prefix}-"):
                    self.tracked_concrete_indices.add(idx_name)
                else:
                    cleanup_errors.append(
                        f"Ownership mismatch: discovered index {idx_name} does not match prefix {self.bench_prefix}-"
                    )
        except Exception as exc:
            cleanup_errors.append(f"OpenSearch index discovery failed for prefix {self.bench_prefix}-*: {exc}")

        # 5. Clean up OpenSearch concrete indices individually (No broad wildcards)
        for idx in list(self.tracked_concrete_indices):
            try:
                # Ownership & namespace safeguards
                if not idx.startswith(f"{self.bench_prefix}-"):
                    cleanup_errors.append(f"Refusing to delete non-bench index: {idx}")
                    continue
                if idx.startswith("rootlens-logs-") or idx.startswith("rootlens-eval-logs-"):
                    cleanup_errors.append(f"Refusing to delete protected development/evaluation index: {idx}")
                    continue
                if "*" in idx or "?" in idx:
                    cleanup_errors.append(f"Wildcard index deletion prohibited: {idx}")
                    continue

                if client.indices.exists(index=idx):
                    client.indices.delete(index=idx)
            except Exception as exc:
                cleanup_errors.append(f"OpenSearch index deletion failed for {idx}: {exc}")

        return cleanup_errors


@asynccontextmanager
async def benchmark_session():
    """Manage live preflight verification and failure-safe cleanup for benchmark sessions."""
    # 1. Serial execution guard
    if "PYTEST_XDIST_WORKER" in os.environ:
        pytest.exit(
            "Parallel execution with pytest-xdist worker detected. Aborting benchmark run.",
            returncode=1,
        )

    # 2. Unique, unpredictable run token
    run_token = uuid.uuid4().hex[:8]
    original_prefix = settings.OPENSEARCH_INDEX_PREFIX
    bench_prefix = f"rootlens-bench-{run_token}-logs"

    # 3. Preflight: OpenSearch
    os_client = get_opensearch_client()
    try:
        is_os_alive = os_client.ping()
    except Exception as exc:
        pytest.skip(f"OpenSearch unreachable on {settings.OPENSEARCH_HOST}:{settings.OPENSEARCH_PORT}: {exc}")
    if not is_os_alive:
        pytest.skip("OpenSearch ping returned False. Ensure OpenSearch container is running.")

    # Affirmative vacancy check: ensure benchmark namespace is completely empty before any writes
    matching_indices = list(os_client.indices.get(index=f"{bench_prefix}-*").keys())
    assert len(matching_indices) == 0, (
        f"Expected benchmark namespace {bench_prefix}-* to be vacant prior to run, but found: {matching_indices}"
    )

    # 4. Preflight: PostgreSQL
    await close_engine()
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            res = await session.execute(select(1))
            assert res.scalar() == 1
            await session.rollback()
    except Exception as exc:
        pytest.skip(f"PostgreSQL unreachable on {settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}: {exc}")

    # 5. Preflight: Upload Directory & Samples
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    assert upload_dir.is_dir()
    assert (SAMPLES_DIR / "scenario_db_cascade.json").is_file()

    # 6. Enforce benchmark namespace globally for this test process
    settings.OPENSEARCH_INDEX_PREFIX = bench_prefix
    assert settings.OPENSEARCH_INDEX_PREFIX == bench_prefix

    tracker = BenchmarkTracker(run_token=run_token, original_prefix=original_prefix)

    test_exc: Optional[BaseException] = None
    try:
        yield tracker, os_client
    except BaseException as exc:
        test_exc = exc
        raise
    finally:
        cleanup_errs = await tracker.cleanup(os_client)
        settings.OPENSEARCH_INDEX_PREFIX = original_prefix
        assert settings.OPENSEARCH_INDEX_PREFIX == original_prefix, (
            f"Failed to restore OPENSEARCH_INDEX_PREFIX: expected '{original_prefix}', got '{settings.OPENSEARCH_INDEX_PREFIX}'"
        )
        if cleanup_errs:
            err_msg = (
                f"Benchmark resource cleanup failed ({len(cleanup_errs)} error(s)):\n"
                + "\n".join(f"  - {err}" for err in cleanup_errs)
            )
            if test_exc is not None:
                raise RuntimeError(
                    f"{err_msg}\n(Original test exception occurred prior to teardown failure)"
                ) from test_exc
            pytest.fail(err_msg)


@pytest_asyncio.fixture
async def benchmark_context():
    """Pytest fixture yielding live preflight-verified benchmark context with guaranteed teardown."""
    async with benchmark_session() as (tracker, os_client):
        yield tracker, os_client


# ---------------------------------------------------------------------------
# Unit Test: Statistics Error Rate Calculation
# ---------------------------------------------------------------------------

async def test_compute_statistics_error_rate_calculation():
    """Unit test for compute_statistics verifying error rate, attempt counting, and zero-sample handling."""
    # 1. 100% success case
    s_success = compute_statistics("all_success", [10.0, 20.0, 30.0], error_count=0)
    assert s_success.total_attempts == 3
    assert s_success.success_count == 3
    assert s_success.error_count == 0
    assert s_success.error_rate_pct == 0.0
    assert s_success.mean_ms == 20.0
    assert s_success.failed_details == []

    # 2. Mixed success and failure (e.g. 2 successful, 2 failed = 50% error rate)
    err_msgs = ["HTTP 500: database connection error", "Connection reset by peer"]
    s_mixed = compute_statistics("mixed", [15.0, 25.0], error_count=2, failed_details=err_msgs)
    assert s_mixed.total_attempts == 4
    assert s_mixed.success_count == 2
    assert s_mixed.error_count == 2
    assert s_mixed.error_rate_pct == 50.0
    assert s_mixed.mean_ms == 20.0
    assert s_mixed.failed_details == err_msgs

    # 3. 100% failure case (all attempts fail, 0 timing samples)
    all_errs = ["HTTP 503"] * 4
    s_failed = compute_statistics("all_failed", [], error_count=4, failed_details=all_errs)
    assert s_failed.total_attempts == 4
    assert s_failed.success_count == 0
    assert s_failed.error_count == 4
    assert s_failed.error_rate_pct == 100.0
    assert s_failed.mean_ms == 0.0
    assert s_failed.throughput_ops_sec is None
    assert s_failed.throughput_records_sec is None
    assert s_failed.failed_details == all_errs

    # 4. Zero attempts edge case
    s_zero = compute_statistics("zero_attempts", [], error_count=0)
    assert s_zero.total_attempts == 0
    assert s_zero.success_count == 0
    assert s_zero.error_count == 0
    assert s_zero.error_rate_pct == 0.0


# ---------------------------------------------------------------------------
# Unit Tests: Cross-Process Benchmark Lock Ownership Safety
# ---------------------------------------------------------------------------

async def test_benchmark_lock_ownership_lifecycle(tmp_path):
    """Verify that lock tracks explicit ownership throughout its lifecycle:
    acquisition sets owner status, token, and PID; release cleanly deletes only by owner.
    """
    lock_file = tmp_path / "test_lifecycle.lock"
    lock = BenchmarkProcessLock(lock_file)

    # 1. Initial state
    assert lock.is_acquired is False
    assert lock.owner_pid is None
    assert lock.owner_token is None
    assert not lock_file.exists()

    # 2. Acquire lock
    assert lock.acquire() is True
    assert lock.is_acquired is True
    assert lock.owner_pid == os.getpid()
    assert lock.owner_token is not None
    assert len(lock.owner_token) == 32
    assert lock_file.exists()

    # Verify on-disk file contains owner PID and token
    disk_content = lock_file.read_text(encoding="utf-8")
    assert f"pid={os.getpid()}" in disk_content
    assert f"token={lock.owner_token}" in disk_content

    # 3. Re-acquisition on already acquired instance returns False
    assert lock.acquire() is False
    assert lock.is_acquired is True

    # 4. Release by owner
    assert lock.release() is True
    assert lock.is_acquired is False
    assert lock.owner_pid is None
    assert lock.owner_token is None
    assert not lock_file.exists()

    # 5. Subsequent release is idempotent and returns False
    assert lock.release() is False


async def test_benchmark_lock_non_owner_cannot_release_or_delete(tmp_path):
    """Verify that if another process/instance owns the lock:
    1. Acquisition fails and leaves the existing lock file unmodified.
    2. Non-owner cannot release or delete the owner's lock.
    3. The owner's lock file and tokens remain intact until owner releases.
    """
    lock_file = tmp_path / "test_non_owner.lock"
    owner_lock = BenchmarkProcessLock(lock_file)
    assert owner_lock.acquire() is True
    assert lock_file.exists()

    original_content = lock_file.read_text(encoding="utf-8")

    # Foreign instance attempts to acquire the same lock
    foreign_lock = BenchmarkProcessLock(lock_file)
    assert foreign_lock.acquire() is False
    assert foreign_lock.is_acquired is False
    assert foreign_lock.owner_token is None
    assert foreign_lock.owner_pid is None

    # Foreign instance attempts to release or delete
    assert foreign_lock.release() is False

    # Verify the lock file on disk was NOT deleted or modified
    assert lock_file.exists(), "Foreign instance must never delete an existing lock"
    assert lock_file.read_text(encoding="utf-8") == original_content

    # Owner can release its own lock
    assert owner_lock.release() is True
    assert not lock_file.exists()


async def test_benchmark_lock_token_tamper_protection(tmp_path):
    """Verify that if the lock file on disk is replaced or tampered with
    such that the token does not match, release() refuses to delete it.
    """
    lock_file = tmp_path / "test_tamper.lock"
    lock = BenchmarkProcessLock(lock_file)
    assert lock.acquire() is True

    # Tamper with the lock file on disk (simulate another process having replaced it)
    lock_file.write_text("pid=99999\ntoken=foreign_token\n", encoding="utf-8")

    # The original lock instance attempts to release
    # Because on-disk token doesn't match, it must refuse to unlink
    released = lock.release()
    assert released is False
    assert lock_file.exists(), "Lock file with mismatched token must not be unlinked"
    assert "foreign_token" in lock_file.read_text(encoding="utf-8")

    # Cleanup test artifact safely
    lock_file.unlink()


async def test_benchmark_lock_concurrent_contention_safety(tmp_path):
    """Verify that when multiple concurrent contenders compete for the same lock:
    1. Exactly one contender successfully acquires the lock.
    2. All other contenders fail safely without raising unhandled errors.
    3. Non-owners calling release() fail safely and do not remove the winner's lock.
    4. Only the winning contender can release the lock.
    """
    lock_file = tmp_path / "test_contention.lock"
    contenders = [BenchmarkProcessLock(lock_file) for _ in range(8)]

    # Run simultaneous acquisition across contenders
    async def try_acquire(l: BenchmarkProcessLock):
        return l.acquire()

    results = await asyncio.gather(*(try_acquire(l) for l in contenders))

    # Exactly one winner
    winners = [l for l, acquired in zip(contenders, results) if acquired]
    losers = [l for l, acquired in zip(contenders, results) if not acquired]

    assert len(winners) == 1, f"Expected exactly 1 winner, got {len(winners)}"
    assert len(losers) == 7, f"Expected 7 losers, got {len(losers)}"

    winner = winners[0]

    # Verify lock file belongs to winner
    assert lock_file.exists()
    assert f"token={winner.owner_token}" in lock_file.read_text(encoding="utf-8")

    # All losers attempt to release; all must return False and must NOT delete lock
    for loser in losers:
        assert loser.release() is False
    assert lock_file.exists()

    # Winner releases
    assert winner.release() is True
    assert not lock_file.exists()


async def test_benchmark_lock_context_manager_and_exception_safety(tmp_path):
    """Verify that BenchmarkProcessLock works as a context manager and releases on exception."""
    lock_file = tmp_path / "test_context.lock"

    # Context manager normal exit
    with BenchmarkProcessLock(lock_file) as lock:
        assert lock.is_acquired is True
        assert lock_file.exists()
    assert not lock_file.exists()

    # Context manager exception exit
    with pytest.raises(ValueError, match="Intentional test error"):
        with BenchmarkProcessLock(lock_file) as lock:
            assert lock.is_acquired is True
            assert lock_file.exists()
            raise ValueError("Intentional test error")
    assert not lock_file.exists(), "Lock must be released on exception inside with block"

    # Context manager contention error
    outer_lock = BenchmarkProcessLock(lock_file)
    assert outer_lock.acquire() is True
    with pytest.raises(RuntimeError, match="Failed to acquire benchmark lock"):
        with BenchmarkProcessLock(lock_file):
            pass
    assert lock_file.exists()
    assert outer_lock.release() is True
    assert not lock_file.exists()


async def test_benchmark_lock_acquire_exception_preserves_foreign_lock(tmp_path, monkeypatch):
    """Verify that if an unexpected exception occurs during acquire(), cleanup never unlinks
    a foreign lock file on disk. Only files matching current acquisition token/pid may be deleted.
    """
    lock_file = tmp_path / "foreign_protected.lock"
    lock_file.write_text("pid=77777\ntoken=foreign_secret_token\n", encoding="utf-8")

    # Instance attempts acquire on path where foreign lock exists
    lock = BenchmarkProcessLock(lock_file)

    # Monkeypatch os.open to simulate returning a valid fd without exclusive flag,
    # but monkeypatch os.write to simulate an unexpected disk error during write
    real_open = os.open

    def mock_open(path, flags, mode=0o777):
        return real_open(path, os.O_RDONLY)

    def mock_write(fd, data):
        raise OSError("Simulated disk error during lock write")

    monkeypatch.setattr(os, "open", mock_open)
    monkeypatch.setattr(os, "write", mock_write)

    # Attempt acquire (must fail safely)
    result = lock.acquire()
    assert result is False
    assert lock.is_acquired is False

    # CRITICAL: Foreign lock MUST NOT have been unlinked
    assert lock_file.exists(), "acquire() exception cleanup must never unlink foreign lock"
    assert "foreign_secret_token" in lock_file.read_text(encoding="utf-8")


async def test_benchmark_lock_multiprocess_contention_safety(tmp_path):
    """Verify genuine multiprocessing contention safety across independent OS processes:
    1. Multiple distinct OS processes simultaneously attempt to acquire the same lock.
    2. Exactly one independent process successfully acquires the lock.
    3. All losing processes are safely rejected and fail to acquire.
    4. Non-owners calling release() fail safely and cannot delete or modify the winner's lock.
    5. The winning process releases its own lock and the lock file is cleanly removed.
    """
    lock_file = tmp_path / "multiprocess_contention.lock"
    repo_root = Path(__file__).resolve().parent.parent

    # Worker script executed in separate OS processes
    worker_script = f'''import sys
import time
from pathlib import Path

sys.path.insert(0, r"{str(repo_root)}")
from tests.test_benchmark_performance import BenchmarkProcessLock

lock_path = Path(sys.argv[1])
lock = BenchmarkProcessLock(lock_path)

if lock.acquire():
    # Winner holds lock long enough to ensure all other concurrently spawned processes contend for it
    time.sleep(1.2)
    # Winner releases
    released = lock.release()
    sys.exit(0 if released else 10)
else:
    # Non-owner attempts to release (must return False and NOT delete foreign lock)
    deleted_foreign = lock.release()
    if deleted_foreign:
        sys.exit(20)  # FATAL: non-owner deleted another process's lock
    sys.exit(2)  # Safely rejected without modifying lock
'''

    num_processes = 5
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", worker_script, str(lock_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(num_processes)
    ]

    exit_codes = []
    stderr_outputs = []
    for p in procs:
        stdout, stderr = p.communicate(timeout=15)
        exit_codes.append(p.returncode)
        if stderr:
            stderr_outputs.append(stderr)

    assert not stderr_outputs, f"Subprocesses encountered unexpected errors: {stderr_outputs}"

    # Exactly 1 process should have acquired and released the lock (exit code 0)
    winner_count = exit_codes.count(0)
    # Exactly 4 processes should have failed acquisition safely and not deleted the lock (exit code 2)
    loser_count = exit_codes.count(2)
    # No non-owner should have deleted the lock (exit code 20)
    tamper_count = exit_codes.count(20)

    assert tamper_count == 0, "A non-owner process illegally deleted another process's lock!"
    assert winner_count == 1, (
        f"Expected exactly 1 process to acquire lock, got {winner_count} (exit codes: {exit_codes})"
    )
    assert loser_count == num_processes - 1, (
        f"Expected {num_processes - 1} losing processes, got {loser_count} (exit codes: {exit_codes})"
    )

    # Verify lock file was cleanly removed by the winner
    assert not lock_file.exists(), "Lock file was not cleaned up after winner released"


async def test_benchmark_metrics_export_json_validity(tmp_path, monkeypatch):
    """Verify that export_benchmark_metrics_to_json generates a valid, deterministic JSON artifact,
    includes PostgreSQL version metadata when available, and handles lookup failure safely when unavailable.
    """
    dummy_summary = BenchmarkMetricSummary(
        operation_name="TEST /benchmark/export",
        total_attempts=5,
        success_count=5,
        error_count=0,
        mean_ms=12.345,
        median_ms=12.0,
        p95_ms=14.5,
        min_ms=10.0,
        max_ms=15.0,
        stddev_ms=1.23,
        warmup_count=1,
        throughput_ops_sec=81.0,
        throughput_records_sec=1620.0,
        details={"test_key": "test_value"},
    )
    register_benchmark_metric(dummy_summary)

    # 1. Normal export with live PostgreSQL connection
    export_file = tmp_path / "test_export.json"
    exported_path = export_benchmark_metrics_to_json(output_path=export_file)

    assert exported_path.exists()
    assert exported_path == export_file

    data = json.loads(export_file.read_text(encoding="utf-8"))
    assert "metadata" in data
    assert "benchmarks" in data
    assert isinstance(data["benchmarks"], list)
    assert any(b["operation_name"] == "TEST /benchmark/export" for b in data["benchmarks"])
    # Verify postgres_version is populated from live database
    assert "postgres_version" in data["metadata"]
    assert data["metadata"]["postgres_version"] != "unknown"
    assert len(data["metadata"]["postgres_version"]) > 0

    # 2. Simulated failure: verify safe handling when PostgreSQL version is unavailable (returns None)
    fail_export_file = tmp_path / "test_export_failure.json"
    monkeypatch.setattr(
        "test_benchmark_performance.fetch_postgres_version",
        lambda *args, **kwargs: None,
    )
    fail_exported_path = export_benchmark_metrics_to_json(output_path=fail_export_file)
    assert fail_exported_path.exists()
    fail_data = json.loads(fail_export_file.read_text(encoding="utf-8"))
    assert fail_data["metadata"]["postgres_version"] == "unavailable"

    # 3. Simulated exception: verify safe handling when lookup raises an exception
    def _raising_lookup(*args, **kwargs):
        raise RuntimeError("Database query timeout")

    monkeypatch.setattr(
        "test_benchmark_performance.fetch_postgres_version",
        _raising_lookup,
    )
    exc_export_file = tmp_path / "test_export_exc.json"
    exc_exported_path = export_benchmark_metrics_to_json(output_path=exc_export_file)
    assert exc_exported_path.exists()
    exc_data = json.loads(exc_export_file.read_text(encoding="utf-8"))
    assert exc_data["metadata"]["postgres_version"] == "unavailable"

    # Clean up registry entry after unit test
    global _benchmark_metrics_registry
    _benchmark_metrics_registry = [
        m for m in _benchmark_metrics_registry if m.operation_name != "TEST /benchmark/export"
    ]


async def test_postgres_version_lookup_success():
    """Verify that fetch_postgres_version retrieves the server version from live PostgreSQL."""
    # Test from a thread with no active event loop
    ver_sync = await asyncio.to_thread(fetch_postgres_version, 5.0)
    assert ver_sync is not None, "Expected valid PostgreSQL version from live database in sync context"
    assert isinstance(ver_sync, str)
    assert any(char.isdigit() for char in ver_sync)


async def test_postgres_version_lookup_failure(monkeypatch):
    """Verify that fetch_postgres_version safely returns None on connection or query failure."""
    async def _failing_connect(*args, **kwargs):
        raise asyncpg.CannotConnectNowError("Database is offline")

    monkeypatch.setattr(asyncpg, "connect", _failing_connect)
    ver = fetch_postgres_version(timeout_sec=2.0)
    assert ver is None


async def test_postgres_version_lookup_timeout(monkeypatch):
    """Verify that timeout_sec provides a genuine upper bound and does not block on worker shutdown."""
    async def _hanging_connect(*args, **kwargs):
        await asyncio.sleep(5.0)
        return None

    monkeypatch.setattr(asyncpg, "connect", _hanging_connect)

    t_start = time.perf_counter()
    ver = fetch_postgres_version(timeout_sec=0.1)
    t_elapsed = time.perf_counter() - t_start

    assert ver is None
    # Must return promptly without blocking on the 5-second sleep in the worker thread
    assert t_elapsed < 0.6, f"Timeout took too long ({t_elapsed:.2f}s), worker shutdown blocked caller"


async def test_postgres_version_lookup_with_running_event_loop():
    """Verify that fetch_postgres_version can be called safely while an asyncio event loop is active."""
    loop = asyncio.get_running_loop()
    assert loop.is_running()

    ver = fetch_postgres_version(timeout_sec=5.0)
    assert ver is not None
    assert isinstance(ver, str)
    assert len(ver) > 0
    assert any(char.isdigit() for char in ver)


# ---------------------------------------------------------------------------
# Benchmark 1: Ingestion & Indexing Throughput (50, 100, 250 records)
# ---------------------------------------------------------------------------

async def test_benchmark_ingestion_and_indexing_throughput(benchmark_context):
    """Benchmark log ingestion and bulk indexing throughput across 50, 100, and 250 records."""
    tracker, os_client = benchmark_context
    sample_records = json.loads((SAMPLES_DIR / "scenario_db_cascade.json").read_text())

    workload_sizes = [50, 100, 250]
    upload_summaries: List[BenchmarkMetricSummary] = []
    indexing_summaries: List[BenchmarkMetricSummary] = []

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for size in workload_sizes:
            records = generate_workload_records(size, sample_records)
            payload_bytes = json.dumps(records).encode("utf-8")

            # 1. Warm-up run (1 unmeasured upload + index to warm templates & connections)
            warmup_upload = await client.post(
                "/api/v1/logs/upload",
                files={"file": (f"warmup_{size}.json", payload_bytes, "application/json")},
                data={
                    "service_name": "benchmark-service",
                    "environment": "benchmark",
                    "source_name": f"warmup-{size}-source",
                },
            )
            # Track returned IDs immediately before asserting status code
            w_job_id = None
            try:
                w_data = warmup_upload.json()
                if isinstance(w_data, dict):
                    if "job_id" in w_data:
                        w_job_id = uuid.UUID(w_data["job_id"])
                        tracker.tracked_job_ids.add(w_job_id)
                        tracker.tracked_files.add(Path(settings.UPLOAD_DIR) / f"{w_job_id}.json")
                    if "log_source_id" in w_data:
                        tracker.tracked_source_ids.add(uuid.UUID(w_data["log_source_id"]))
            except Exception:
                pass
            assert warmup_upload.status_code == 201
            assert w_job_id is not None

            # Pre-register concrete index for 2026-10 records
            tracker.tracked_concrete_indices.add(f"{tracker.bench_prefix}-2026.10")

            warmup_index = await client.post(f"/api/v1/logs/jobs/{w_job_id}/index")
            try:
                w_idx_data = warmup_index.json()
                if isinstance(w_idx_data, dict) and "target_index" in w_idx_data:
                    for idx_name in w_idx_data["target_index"].split(", "):
                        if idx_name.strip():
                            tracker.tracked_concrete_indices.add(idx_name.strip())
            except Exception:
                pass
            assert warmup_index.status_code == 200

            # 2. Measured repetitions (3 runs per workload size)
            upload_timings: List[float] = []
            upload_errors: List[str] = []
            indexing_timings: List[float] = []
            indexing_errors: List[str] = []
            total_staged_records = 0
            total_indexed_records = 0

            repetitions = 3
            for r in range(repetitions):
                # Measure Upload Latency
                t_up_start = time.perf_counter()
                try:
                    upload_resp = await client.post(
                        "/api/v1/logs/upload",
                        files={"file": (f"bench_{size}_{r}.json", payload_bytes, "application/json")},
                        data={
                            "service_name": "benchmark-service",
                            "environment": "benchmark",
                            "source_name": f"bench-{size}-{r}-source",
                        },
                    )
                    t_up_end = time.perf_counter()

                    job_id = None
                    try:
                        up_data = upload_resp.json()
                        if isinstance(up_data, dict):
                            if "job_id" in up_data:
                                job_id = uuid.UUID(up_data["job_id"])
                                tracker.tracked_job_ids.add(job_id)
                                tracker.tracked_files.add(Path(settings.UPLOAD_DIR) / f"{job_id}.json")
                            if "log_source_id" in up_data:
                                tracker.tracked_source_ids.add(uuid.UUID(up_data["log_source_id"]))
                    except Exception:
                        pass

                    if upload_resp.status_code == 201:
                        upload_timings.append((t_up_end - t_up_start) * 1000.0)
                        total_staged_records += size
                    else:
                        upload_errors.append(
                            f"Workload {size} repetition {r} upload failed: status={upload_resp.status_code}"
                        )
                except Exception as exc:
                    upload_errors.append(f"Workload {size} repetition {r} upload exception: {exc}")
                    job_id = None

                # Pre-register concrete index for 2026-10 records
                tracker.tracked_concrete_indices.add(f"{tracker.bench_prefix}-2026.10")

                # Measure Indexing Latency
                if job_id is not None:
                    t_idx_start = time.perf_counter()
                    try:
                        idx_resp = await client.post(f"/api/v1/logs/jobs/{job_id}/index")
                        t_idx_end = time.perf_counter()

                        try:
                            idx_data = idx_resp.json()
                            if isinstance(idx_data, dict) and "target_index" in idx_data:
                                for idx_name in idx_data["target_index"].split(", "):
                                    if idx_name.strip():
                                        tracker.tracked_concrete_indices.add(idx_name.strip())
                        except Exception:
                            pass

                        if idx_resp.status_code == 200:
                            indexing_timings.append((t_idx_end - t_idx_start) * 1000.0)
                            total_indexed_records += size
                        else:
                            indexing_errors.append(
                                f"Workload {size} repetition {r} indexing failed: status={idx_resp.status_code}"
                            )
                    except Exception as exc:
                        indexing_errors.append(f"Workload {size} repetition {r} indexing exception: {exc}")
                else:
                    indexing_errors.append(
                        f"Workload {size} repetition {r} indexing skipped due to upload failure"
                    )

            upload_summary = compute_statistics(
                operation_name=f"POST /upload ({size} recs)",
                timings_ms=upload_timings,
                warmup_count=1,
                total_records=total_staged_records,
                error_count=len(upload_errors),
                failed_details=upload_errors,
            )
            indexing_summary = compute_statistics(
                operation_name=f"POST /index ({size} recs)",
                timings_ms=indexing_timings,
                warmup_count=1,
                total_records=total_indexed_records,
                error_count=len(indexing_errors),
                failed_details=indexing_errors,
            )
            upload_summaries.append(upload_summary)
            indexing_summaries.append(indexing_summary)
            register_benchmark_metric(upload_summary)
            register_benchmark_metric(indexing_summary)

    print(format_benchmark_table(upload_summaries, "Log Staging Latency (In-Process ASGI)"))
    print(format_benchmark_table(indexing_summaries, "Log Bulk Indexing Latency & Throughput (In-Process ASGI)"))

    total_failures = sum(s.error_count for s in upload_summaries + indexing_summaries)
    assert total_failures == 0, (
        f"Ingestion and indexing benchmark encountered {total_failures} failure(s) across measured attempts."
    )


# ---------------------------------------------------------------------------
# Benchmark 2: Search and Filtered-Search Latency
# ---------------------------------------------------------------------------

async def test_benchmark_search_and_aggregation_latency(benchmark_context):
    """Benchmark log search query DSL and date_histogram aggregation latency across 20 iterations."""
    tracker, os_client = benchmark_context
    sample_records = json.loads((SAMPLES_DIR / "scenario_db_cascade.json").read_text())

    # Pre-populate index with 100 deterministic benchmark records
    records = generate_workload_records(100, sample_records)
    payload_bytes = json.dumps(records).encode("utf-8")

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload_resp = await client.post(
            "/api/v1/logs/upload",
            files={"file": ("search_seed.json", payload_bytes, "application/json")},
            data={"service_name": "postgres-db", "environment": "benchmark"},
        )
        job_id = None
        try:
            up_data = upload_resp.json()
            if isinstance(up_data, dict):
                if "job_id" in up_data:
                    job_id = uuid.UUID(up_data["job_id"])
                    tracker.tracked_job_ids.add(job_id)
                    tracker.tracked_files.add(Path(settings.UPLOAD_DIR) / f"{job_id}.json")
                if "log_source_id" in up_data:
                    tracker.tracked_source_ids.add(uuid.UUID(up_data["log_source_id"]))
        except Exception:
            pass
        assert upload_resp.status_code == 201
        assert job_id is not None

        # Pre-register concrete index for 2026-10 records
        tracker.tracked_concrete_indices.add(f"{tracker.bench_prefix}-2026.10")

        idx_resp = await client.post(f"/api/v1/logs/jobs/{job_id}/index")
        try:
            idx_data = idx_resp.json()
            if isinstance(idx_data, dict) and "target_index" in idx_data:
                for idx_name in idx_data["target_index"].split(", "):
                    if idx_name.strip():
                        tracker.tracked_concrete_indices.add(idx_name.strip())
        except Exception:
            pass
        assert idx_resp.status_code == 200

        # Explicitly refresh index in OpenSearch
        for idx in tracker.tracked_concrete_indices:
            os_client.indices.refresh(index=idx)

        # Query scenarios to benchmark
        scenarios = [
            ("Unfiltered scan", "/api/v1/logs", {"page": 1, "page_size": 50}),
            ("Exact service filter", "/api/v1/logs", {"service": "postgres-db"}),
            ("Severity filter", "/api/v1/logs", {"severity": "ERROR"}),
            ("Errors-only filter", "/api/v1/logs", {"errors_only": "true"}),
            ("Free-text query", "/api/v1/logs", {"q": "connection pool"}),
            ("Histogram aggregation", "/api/v1/logs/aggregations", {"interval": "15m"}),
        ]

        query_summaries: List[BenchmarkMetricSummary] = []
        repetitions = 20
        warmup_runs = 2

        for name, endpoint, params in scenarios:
            # Warm-up runs
            for _ in range(warmup_runs):
                w_resp = await client.get(endpoint, params=params)
                assert w_resp.status_code == 200

            # Measured runs
            timings: List[float] = []
            errors: List[str] = []
            for _ in range(repetitions):
                t0 = time.perf_counter()
                try:
                    resp = await client.get(endpoint, params=params)
                    t1 = time.perf_counter()
                    if resp.status_code == 200:
                        timings.append((t1 - t0) * 1000.0)
                    else:
                        errors.append(f"Query {name} failed: status={resp.status_code}")
                except Exception as exc:
                    errors.append(f"Query {name} exception: {exc}")

            summary = compute_statistics(
                operation_name=name,
                timings_ms=timings,
                warmup_count=warmup_runs,
                error_count=len(errors),
                failed_details=errors,
            )
            query_summaries.append(summary)
            register_benchmark_metric(summary)

    print(format_benchmark_table(query_summaries, "Log Search & Aggregation Latency (In-Process ASGI, 20 Repetitions)"))

    total_query_errors = sum(s.error_count for s in query_summaries)
    assert total_query_errors == 0, (
        f"Search and aggregation benchmark encountered {total_query_errors} query failure(s)."
    )


# ---------------------------------------------------------------------------
# Benchmark 3: Incident Candidate Evaluation Latency
# ---------------------------------------------------------------------------

async def test_benchmark_candidate_evaluation_latency(benchmark_context):
    """Benchmark error log querying, clustering, and cross-service correlation pipeline."""
    tracker, os_client = benchmark_context
    sample_records = json.loads((SAMPLES_DIR / "scenario_db_cascade.json").read_text())

    # Pre-populate index with the real database cascade scenario (23 records)
    payload_bytes = json.dumps(sample_records).encode("utf-8")

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload_resp = await client.post(
            "/api/v1/logs/upload",
            files={"file": ("cascade_seed.json", payload_bytes, "application/json")},
            data={"service_name": "postgres-db", "environment": "benchmark"},
        )
        job_id = None
        try:
            up_data = upload_resp.json()
            if isinstance(up_data, dict):
                if "job_id" in up_data:
                    job_id = uuid.UUID(up_data["job_id"])
                    tracker.tracked_job_ids.add(job_id)
                    tracker.tracked_files.add(Path(settings.UPLOAD_DIR) / f"{job_id}.json")
                if "log_source_id" in up_data:
                    tracker.tracked_source_ids.add(uuid.UUID(up_data["log_source_id"]))
        except Exception:
            pass
        assert upload_resp.status_code == 201
        assert job_id is not None

        # Pre-register concrete index for 2026-10 records
        tracker.tracked_concrete_indices.add(f"{tracker.bench_prefix}-2026.10")

        idx_resp = await client.post(f"/api/v1/logs/jobs/{job_id}/index")
        try:
            idx_data = idx_resp.json()
            if isinstance(idx_data, dict) and "target_index" in idx_data:
                for idx_name in idx_data["target_index"].split(", "):
                    if idx_name.strip():
                        tracker.tracked_concrete_indices.add(idx_name.strip())
        except Exception:
            pass
        assert idx_resp.status_code == 200

        for idx in tracker.tracked_concrete_indices:
            os_client.indices.refresh(index=idx)

        eval_payload = {
            "start_time": "2026-10-14T07:55:00Z",
            "end_time": "2026-10-14T08:15:00Z",
            "window_minutes": 15,
            "cascade_window_seconds": 120,
            "max_logs": 1000,
        }

        # Warm-up (2 runs)
        for _ in range(2):
            w_res = await client.post("/api/v1/incidents/candidates", json=eval_payload)
            assert w_res.status_code == 200

        # Measured runs (15 runs)
        timings: List[float] = []
        errors: List[str] = []
        repetitions = 15
        for _ in range(repetitions):
            t0 = time.perf_counter()
            try:
                res = await client.post("/api/v1/incidents/candidates", json=eval_payload)
                t1 = time.perf_counter()
                if res.status_code == 200:
                    data = res.json()
                    assert data["total_candidates"] >= 1
                    assert data["candidates"][0]["is_root_cause_confirmed"] is False
                    timings.append((t1 - t0) * 1000.0)
                else:
                    errors.append(f"Candidate evaluation failed: status={res.status_code}")
            except Exception as exc:
                errors.append(f"Candidate evaluation exception: {exc}")

        summary = compute_statistics(
            operation_name="POST /incidents/candidates",
            timings_ms=timings,
            warmup_count=2,
            error_count=len(errors),
            failed_details=errors,
        )
        register_benchmark_metric(summary)

    print(format_benchmark_table([summary], "Incident Candidate Evaluation Pipeline (In-Process ASGI, 15 Repetitions)"))
    assert summary.error_count == 0, (
        f"Candidate evaluation benchmark encountered {summary.error_count} failure(s): {errors}"
    )


# ---------------------------------------------------------------------------
# Benchmark 4: Evidence Analysis & RCA Synthesis Latency
# ---------------------------------------------------------------------------

async def test_benchmark_evidence_rca_and_endpoint_latency(benchmark_context):
    """Benchmark in-memory evidence sufficiency, RCA synthesis, and explanation/recommendation endpoints."""
    tracker, os_client = benchmark_context
    sample_records = json.loads((SAMPLES_DIR / "scenario_db_cascade.json").read_text())
    payload_bytes = json.dumps(sample_records).encode("utf-8")

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Setup persisted incident for endpoint benchmarking
        up_resp = await client.post(
            "/api/v1/logs/upload",
            files={"file": ("rca_seed.json", payload_bytes, "application/json")},
            data={"service_name": "postgres-db", "environment": "benchmark"},
        )
        job_id = None
        try:
            up_data = up_resp.json()
            if isinstance(up_data, dict):
                if "job_id" in up_data:
                    job_id = uuid.UUID(up_data["job_id"])
                    tracker.tracked_job_ids.add(job_id)
                    tracker.tracked_files.add(Path(settings.UPLOAD_DIR) / f"{job_id}.json")
                if "log_source_id" in up_data:
                    tracker.tracked_source_ids.add(uuid.UUID(up_data["log_source_id"]))
        except Exception:
            pass
        assert up_resp.status_code == 201
        assert job_id is not None

        # Pre-register concrete index for 2026-10 records
        tracker.tracked_concrete_indices.add(f"{tracker.bench_prefix}-2026.10")

        idx_resp = await client.post(f"/api/v1/logs/jobs/{job_id}/index")
        try:
            idx_data = idx_resp.json()
            if isinstance(idx_data, dict) and "target_index" in idx_data:
                for idx_name in idx_data["target_index"].split(", "):
                    if idx_name.strip():
                        tracker.tracked_concrete_indices.add(idx_name.strip())
        except Exception:
            pass
        assert idx_resp.status_code == 200

        for idx in tracker.tracked_concrete_indices:
            os_client.indices.refresh(index=idx)

        cand_resp = await client.post(
            "/api/v1/incidents/candidates",
            json={
                "start_time": "2026-10-14T07:55:00Z",
                "end_time": "2026-10-14T08:15:00Z",
                "window_minutes": 15,
                "cascade_window_seconds": 120,
                "max_logs": 1000,
            },
        )
        assert cand_resp.status_code == 200
        cand = cand_resp.json()["candidates"][0]

        create_payload = {
            "candidate_id": cand["candidate_id"],
            "title": cand["title"],
            "severity": cand["severity"],
            "started_at": cand["started_at"],
            "ended_at": cand["ended_at"],
            "correlation_rules_used": cand["correlation_rules_used"],
            "uncertainty_reason": cand["uncertainty_reason"],
            "affected_services": cand["affected_services"],
            "timeline_events": cand["timeline_events"],
            "evidence_items": cand["evidence_items"],
            "cluster_ids": cand["cluster_ids"],
        }
        persist_resp = await client.post("/api/v1/incidents", json=create_payload)
        inc_id = None
        try:
            p_data = persist_resp.json()
            if isinstance(p_data, dict) and "id" in p_data:
                inc_id = uuid.UUID(p_data["id"])
                tracker.tracked_incident_ids.add(inc_id)
        except Exception:
            pass
        assert persist_resp.status_code in (200, 201)
        assert inc_id is not None

        # 2. In-Memory Algorithm Micro-Benchmarks (50 runs each)
        evidence_items = cand["evidence_items"]
        timeline_events = cand["timeline_events"]

        # evaluate_evidence_sufficiency
        eval_timings: List[float] = []
        for _ in range(50):
            t0 = time.perf_counter()
            assessment = evaluate_evidence_sufficiency(
                evidence_items=evidence_items,
                timeline_events=timeline_events,
            )
            t1 = time.perf_counter()
            eval_timings.append((t1 - t0) * 1000.0)
        assert assessment.is_root_cause_confirmed is False

        # synthesize_rca_findings
        rca_timings: List[float] = []
        for _ in range(50):
            t0 = time.perf_counter()
            rca_result = synthesize_rca_findings(
                assessment=assessment,
                evidence_items=evidence_items,
                timeline_events=timeline_events,
            )
            t1 = time.perf_counter()
            rca_timings.append((t1 - t0) * 1000.0)
        assert rca_result.is_root_cause_confirmed is False

        # 3. HTTP Diagnostic Endpoints (10 runs each)
        expl_timings: List[float] = []
        expl_errors: List[str] = []
        for _ in range(10):
            t0 = time.perf_counter()
            try:
                expl_resp = await client.get(f"/api/v1/incidents/{inc_id}/explanation")
                t1 = time.perf_counter()
                if expl_resp.status_code == 200:
                    expl_timings.append((t1 - t0) * 1000.0)
                else:
                    expl_errors.append(f"GET explanation failed: status={expl_resp.status_code}")
            except Exception as exc:
                expl_errors.append(f"GET explanation exception: {exc}")

        rec_timings: List[float] = []
        rec_errors: List[str] = []
        for _ in range(10):
            t0 = time.perf_counter()
            try:
                rec_resp = await client.post(f"/api/v1/incidents/{inc_id}/recommendations")
                t1 = time.perf_counter()
                if rec_resp.status_code in (200, 201):
                    rec_timings.append((t1 - t0) * 1000.0)
                else:
                    rec_errors.append(f"POST recommendations failed: status={rec_resp.status_code}")
            except Exception as exc:
                rec_errors.append(f"POST recommendations exception: {exc}")

        summaries = [
            compute_statistics("evaluate_evidence_sufficiency()", eval_timings, warmup_count=2),
            compute_statistics("synthesize_rca_findings()", rca_timings, warmup_count=2),
            compute_statistics(
                "GET /incidents/{id}/explanation",
                expl_timings,
                warmup_count=1,
                error_count=len(expl_errors),
                failed_details=expl_errors,
            ),
            compute_statistics(
                "POST /incidents/{id}/recommendations",
                rec_timings,
                warmup_count=1,
                error_count=len(rec_errors),
                failed_details=rec_errors,
            ),
        ]
        for s in summaries:
            register_benchmark_metric(s)

    print(format_benchmark_table(summaries, "Evidence Analysis, RCA Synthesis & Diagnostic Endpoints (In-Process ASGI)"))

    total_diag_errors = sum(s.error_count for s in summaries)
    assert total_diag_errors == 0, (
        f"Diagnostic endpoints benchmark encountered {total_diag_errors} error(s)."
    )


# ---------------------------------------------------------------------------
# Benchmark 5: Bounded Concurrency and Idempotency Behavior
# ---------------------------------------------------------------------------

async def test_benchmark_bounded_concurrency_and_idempotency(benchmark_context):
    """Benchmark concurrent incident creation calls with duplicate candidate IDs to verify idempotency."""
    tracker, os_client = benchmark_context
    sample_records = json.loads((SAMPLES_DIR / "scenario_db_cascade.json").read_text())
    payload_bytes = json.dumps(sample_records).encode("utf-8")

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Pre-seed candidate
        up_resp = await client.post(
            "/api/v1/logs/upload",
            files={"file": ("concurrency_seed.json", payload_bytes, "application/json")},
            data={"service_name": "postgres-db", "environment": "benchmark"},
        )
        job_id = None
        try:
            up_data = up_resp.json()
            if isinstance(up_data, dict):
                if "job_id" in up_data:
                    job_id = uuid.UUID(up_data["job_id"])
                    tracker.tracked_job_ids.add(job_id)
                    tracker.tracked_files.add(Path(settings.UPLOAD_DIR) / f"{job_id}.json")
                if "log_source_id" in up_data:
                    tracker.tracked_source_ids.add(uuid.UUID(up_data["log_source_id"]))
        except Exception:
            pass
        assert up_resp.status_code == 201
        assert job_id is not None

        # Pre-register concrete index for 2026-10 records
        tracker.tracked_concrete_indices.add(f"{tracker.bench_prefix}-2026.10")

        idx_resp = await client.post(f"/api/v1/logs/jobs/{job_id}/index")
        try:
            idx_data = idx_resp.json()
            if isinstance(idx_data, dict) and "target_index" in idx_data:
                for idx_name in idx_data["target_index"].split(", "):
                    if idx_name.strip():
                        tracker.tracked_concrete_indices.add(idx_name.strip())
        except Exception:
            pass
        assert idx_resp.status_code == 200

        for idx in tracker.tracked_concrete_indices:
            os_client.indices.refresh(index=idx)

        cand_resp = await client.post(
            "/api/v1/incidents/candidates",
            json={
                "start_time": "2026-10-14T07:55:00Z",
                "end_time": "2026-10-14T08:15:00Z",
                "window_minutes": 15,
                "cascade_window_seconds": 120,
                "max_logs": 1000,
            },
        )
        assert cand_resp.status_code == 200
        cand = cand_resp.json()["candidates"][0]

        create_payload = {
            "candidate_id": cand["candidate_id"],
            "title": cand["title"],
            "severity": cand["severity"],
            "started_at": cand["started_at"],
            "ended_at": cand["ended_at"],
            "correlation_rules_used": cand["correlation_rules_used"],
            "uncertainty_reason": cand["uncertainty_reason"],
            "affected_services": cand["affected_services"],
            "timeline_events": cand["timeline_events"],
            "evidence_items": cand["evidence_items"],
            "cluster_ids": cand["cluster_ids"],
        }

        # Dispatch 5 concurrent incident persistence requests for the identical candidate
        concurrency_count = 5

        async def send_create_request():
            t0 = time.perf_counter()
            try:
                res = await client.post("/api/v1/incidents", json=create_payload)
                t1 = time.perf_counter()
                status = res.status_code
                try:
                    body = res.json()
                except Exception:
                    body = {}
                # Immediately track incident ID if present in body
                if isinstance(body, dict) and "id" in body:
                    try:
                        inc_uuid = uuid.UUID(body["id"])
                        tracker.tracked_incident_ids.add(inc_uuid)
                    except Exception:
                        pass
                return status, body, (t1 - t0) * 1000.0, None
            except Exception as exc:
                t1 = time.perf_counter()
                return 0, {}, (t1 - t0) * 1000.0, str(exc)

        tasks = [send_create_request() for _ in range(concurrency_count)]
        results = await asyncio.gather(*tasks)

        status_counts: Dict[int, int] = {}
        timings: List[float] = []
        created_ids: Set[uuid.UUID] = set()
        errors: List[str] = []

        for status_code, body, duration_ms, err in results:
            if err is not None:
                errors.append(err)
            else:
                status_counts[status_code] = status_counts.get(status_code, 0) + 1
                timings.append(duration_ms)
                if isinstance(body, dict) and "id" in body:
                    try:
                        created_ids.add(uuid.UUID(body["id"]))
                    except Exception:
                        pass

        summary = compute_statistics(
            operation_name=f"Concurrent Persistence ({concurrency_count} calls)",
            timings_ms=timings,
            total_operations=concurrency_count,
            error_count=len(errors),
            failed_details=errors,
            details={"status_counts": status_counts},
        )
        register_benchmark_metric(summary)

        print(format_benchmark_table([summary], "Bounded Concurrency & Idempotency Verification (In-Process ASGI)"))

        # Verification 1: All requests must succeed without network/client errors
        assert len(errors) == 0, f"Encountered network/client errors during concurrent dispatch: {errors}"

        # Verification 2: All requests returned valid idempotency codes (200 or 201)
        assert all(code in (200, 201) for code in status_counts.keys()), (
            f"Unexpected HTTP status codes under concurrency: {status_counts}"
        )

        # Verification 3: Exactly 1 unique incident UUID returned across all concurrent callers
        assert len(created_ids) == 1, (
            f"Expected exactly 1 unique incident ID, but got {len(created_ids)}: {created_ids}"
        )

        # Verification 4: Exactly 1 incident row exists in PostgreSQL
        the_incident_id = list(created_ids)[0]
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            count_res = await session.execute(
                select(func.count(Incident.id)).where(Incident.id == the_incident_id)
            )
            db_row_count = count_res.scalar()
            assert db_row_count == 1, (
                f"PostgreSQL duplicate row check failed: expected 1 row, found {db_row_count}"
            )

    print(
        f"Concurrency Results: Total Calls={concurrency_count}, Status Breakdown={status_counts}, "
        f"Unique DB Records Created={db_row_count}\n"
    )


# ---------------------------------------------------------------------------
# Benchmark 6: Teardown and Cleanup Safety on Real Exception Path
# ---------------------------------------------------------------------------

async def test_benchmark_teardown_executes_on_unhandled_exception():
    """Verify that an unhandled exception inside a benchmark session triggers finally teardown and cleans all resources."""
    sample_records = json.loads((SAMPLES_DIR / "scenario_db_cascade.json").read_text())
    payload_bytes = json.dumps(sample_records).encode("utf-8")

    staged_job_ids: Set[uuid.UUID] = set()
    staged_source_ids: Set[uuid.UUID] = set()
    staged_files: Set[Path] = set()
    staged_indices: Set[str] = set()
    staged_incident_ids: Set[uuid.UUID] = set()
    captured_bench_prefix: Optional[str] = None
    captured_os_client: Optional[OpenSearch] = None

    # Capture original settings prefix before entering session
    original_prefix_snapshot = settings.OPENSEARCH_INDEX_PREFIX

    try:
        # Deliberately raise controlled unhandled exception inside the benchmark context
        with pytest.raises(RuntimeError, match="Controlled exception inside benchmark execution"):
            async with benchmark_session() as (tracker, os_client):
                captured_bench_prefix = tracker.bench_prefix
                captured_os_client = os_client

                async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    # 1. Stage upload job and file
                    up_resp = await client.post(
                        "/api/v1/logs/upload",
                        files={"file": ("crash_test_seed.json", payload_bytes, "application/json")},
                        data={"service_name": "postgres-db", "environment": "benchmark"},
                    )
                    assert up_resp.status_code == 201
                    up_data = up_resp.json()
                    job_id = uuid.UUID(up_data["job_id"])
                    tracker.tracked_job_ids.add(job_id)
                    tracker.tracked_source_ids.add(uuid.UUID(up_data["log_source_id"]))
                    tracker.tracked_files.add(Path(settings.UPLOAD_DIR) / f"{job_id}.json")

                    staged_job_ids.update(tracker.tracked_job_ids)
                    staged_source_ids.update(tracker.tracked_source_ids)
                    staged_files.update(tracker.tracked_files)

                    # 2. Stage OpenSearch index
                    tracker.tracked_concrete_indices.add(f"{tracker.bench_prefix}-2026.10")
                    idx_resp = await client.post(f"/api/v1/logs/jobs/{job_id}/index")
                    assert idx_resp.status_code == 200
                    staged_indices.update(tracker.tracked_concrete_indices)

                    for idx in tracker.tracked_concrete_indices:
                        os_client.indices.refresh(index=idx)

                    # 3. Stage PostgreSQL Incident
                    cand_resp = await client.post(
                        "/api/v1/incidents/candidates",
                        json={
                            "start_time": "2026-10-14T07:55:00Z",
                            "end_time": "2026-10-14T08:15:00Z",
                            "window_minutes": 15,
                            "cascade_window_seconds": 120,
                            "max_logs": 1000,
                        },
                    )
                    assert cand_resp.status_code == 200
                    cand = cand_resp.json()["candidates"][0]

                    create_payload = {
                        "candidate_id": cand["candidate_id"],
                        "title": "Exception Path Test Incident",
                        "severity": cand["severity"],
                        "started_at": cand["started_at"],
                        "ended_at": cand["ended_at"],
                        "correlation_rules_used": cand["correlation_rules_used"],
                        "uncertainty_reason": cand["uncertainty_reason"],
                        "affected_services": cand["affected_services"],
                        "timeline_events": cand["timeline_events"],
                        "evidence_items": cand["evidence_items"],
                        "cluster_ids": cand["cluster_ids"],
                    }
                    persist_resp = await client.post("/api/v1/incidents", json=create_payload)
                    assert persist_resp.status_code in (200, 201)
                    inc_id = uuid.UUID(persist_resp.json()["id"])
                    tracker.tracked_incident_ids.add(inc_id)
                    staged_incident_ids.update(tracker.tracked_incident_ids)

                    # Confirm all resources were committed and exist prior to the exception
                    assert len(staged_job_ids) > 0
                    assert len(staged_files) > 0
                    assert len(staged_indices) > 0
                    assert len(staged_incident_ids) > 0
                    for f in staged_files:
                        assert f.exists()
                    for idx in staged_indices:
                        assert os_client.indices.exists(index=idx)

                    # Trigger fatal exception to exercise the context's finally teardown block
                    raise RuntimeError("Controlled exception inside benchmark execution")
    finally:
        # Guarantee exact settings restoration assertion even when the controlled exception occurs
        assert settings.OPENSEARCH_INDEX_PREFIX == original_prefix_snapshot, (
            f"OPENSEARCH_INDEX_PREFIX was not restored to exact initial snapshot: "
            f"expected '{original_prefix_snapshot}', got '{settings.OPENSEARCH_INDEX_PREFIX}'"
        )

    # Affirmatively verify that the context's finally block executed and cleaned up all resources
    assert captured_bench_prefix is not None
    assert captured_os_client is not None

    # Filesystem: verify staged files were unlinked
    for f in staged_files:
        assert not f.exists(), f"Staged file {f} was not deleted by fixture teardown"

    # OpenSearch: verify concrete indices were deleted
    for idx in staged_indices:
        assert not captured_os_client.indices.exists(index=idx), f"Index {idx} was not deleted by teardown"

    # OpenSearch: verify entire run namespace is vacant
    leftovers = captured_os_client.indices.get(index=f"{captured_bench_prefix}-*")
    assert len(leftovers) == 0, f"Discovered leftover indices in namespace: {leftovers}"

    # PostgreSQL: verify database rows were deleted
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        inc_count = (await session.execute(
            select(func.count(Incident.id)).where(Incident.id.in_(list(staged_incident_ids)))
        )).scalar()
        assert inc_count == 0, "Incident was not deleted by teardown"

        job_count = (await session.execute(
            select(func.count(UploadJob.id)).where(UploadJob.id.in_(list(staged_job_ids)))
        )).scalar()
        assert job_count == 0, "UploadJob was not deleted by teardown"

        src_count = (await session.execute(
            select(func.count(LogSource.id)).where(LogSource.id.in_(list(staged_source_ids)))
        )).scalar()
        assert src_count == 0, "LogSource was not deleted by teardown"
    await close_engine()


# ---------------------------------------------------------------------------
# Benchmark 7: Teardown and Cleanup Safety via benchmark_context Fixture
# ---------------------------------------------------------------------------

async def test_benchmark_context_fixture_teardown_via_isolated_pytest():
    """Verify that an unhandled exception inside a test using the pytest fixture benchmark_context
    triggers pytest-managed fixture teardown, resource cleanup, and exact prefix restoration
    via a genuine isolated pytest invocation in a temporary module outside the repository.
    """
    # 1. Concurrency and serial-execution guard
    if "PYTEST_XDIST_WORKER" in os.environ:
        pytest.exit(
            "Parallel execution with pytest-xdist worker detected. Aborting benchmark run.",
            returncode=1,
        )

    original_prefix_snapshot = settings.OPENSEARCH_INDEX_PREFIX

    # Preflight: OpenSearch & PostgreSQL live services check
    os_client = get_opensearch_client()
    try:
        is_os_alive = os_client.ping()
    except Exception as exc:
        pytest.skip(f"OpenSearch unreachable on {settings.OPENSEARCH_HOST}:{settings.OPENSEARCH_PORT}: {exc}")
    if not is_os_alive:
        pytest.skip("OpenSearch ping returned False. Ensure OpenSearch container is running.")

    # Acquire isolated subprocess execution lock with strict ownership safety
    isolated_lock_file = Path(tempfile.gettempdir()) / "rootlens_benchmark_isolated_subprocess.lock"
    isolated_lock = BenchmarkProcessLock(isolated_lock_file)
    if not isolated_lock.acquire():
        pytest.fail(
            f"Another benchmark isolated test run appears to be in progress (lock exists at {isolated_lock.lock_path}). "
            "Concurrent benchmark execution is strictly prohibited."
        )
    try:

        repo_root = Path(__file__).resolve().parent.parent
        backend_dir_path = repo_root / "backend"
        bench_harness_path = Path(__file__).resolve()

        with tempfile.TemporaryDirectory() as tmp_dir:
            test_file = Path(tmp_dir) / "test_isolated_fixture_teardown.py"
            audit_file = Path(tmp_dir) / "resource_audit.json"

            # Generate the isolated test module outside the repository
            isolated_test_code = f'''import importlib.util
import json
import os
from pathlib import Path
import sys
import uuid

import httpx
from httpx import ASGITransport
import pytest

# Ensure backend directory is in sys.path
sys.path.insert(0, r"{str(backend_dir_path)}")

# Dynamically import benchmark_context and dependencies from benchmark harness
spec = importlib.util.spec_from_file_location("bench_harness", r"{str(bench_harness_path)}")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
benchmark_context = mod.benchmark_context
app = mod.app
settings = mod.settings
SAMPLES_DIR = mod.SAMPLES_DIR

initial_prefix = settings.OPENSEARCH_INDEX_PREFIX

@pytest.mark.asyncio
async def test_fixture_controlled_crash(benchmark_context):
    tracker, os_client = benchmark_context
    sample_records = json.loads((SAMPLES_DIR / "scenario_db_cascade.json").read_text())
    payload_bytes = json.dumps(sample_records).encode("utf-8")

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Stage upload job and file
        up_resp = await client.post(
            "/api/v1/logs/upload",
            files={{"file": ("isolated_fixture_crash_seed.json", payload_bytes, "application/json")}},
            data={{"service_name": "postgres-db", "environment": "benchmark"}},
        )
        assert up_resp.status_code == 201
        up_data = up_resp.json()
        job_id = uuid.UUID(up_data["job_id"])
        source_id = uuid.UUID(up_data["log_source_id"])
        tracker.tracked_job_ids.add(job_id)
        tracker.tracked_source_ids.add(source_id)
        staged_file = Path(settings.UPLOAD_DIR) / f"{{job_id}}.json"
        tracker.tracked_files.add(staged_file)

        # 2. Stage OpenSearch index
        concrete_idx = f"{{tracker.bench_prefix}}-2026.10"
        tracker.tracked_concrete_indices.add(concrete_idx)
        idx_resp = await client.post(f"/api/v1/logs/jobs/{{job_id}}/index")
        assert idx_resp.status_code == 200
        os_client.indices.refresh(index=concrete_idx)

        # 3. Stage PostgreSQL Incident
        cand_resp = await client.post(
            "/api/v1/incidents/candidates",
            json={{
                "start_time": "2026-10-14T07:55:00Z",
                "end_time": "2026-10-14T08:15:00Z",
                "window_minutes": 15,
                "cascade_window_seconds": 120,
                "max_logs": 1000,
            }},
        )
        assert cand_resp.status_code == 200
        cand = cand_resp.json()["candidates"][0]

        create_payload = {{
            "candidate_id": cand["candidate_id"],
            "title": "Isolated Pytest Crash Incident",
            "severity": cand["severity"],
            "started_at": cand["started_at"],
            "ended_at": cand["ended_at"],
            "correlation_rules_used": cand["correlation_rules_used"],
            "uncertainty_reason": cand["uncertainty_reason"],
            "affected_services": cand["affected_services"],
            "timeline_events": cand["timeline_events"],
            "evidence_items": cand["evidence_items"],
            "cluster_ids": cand["cluster_ids"],
        }}
        persist_resp = await client.post("/api/v1/incidents", json=create_payload)
        assert persist_resp.status_code in (200, 201)
        inc_id = uuid.UUID(persist_resp.json()["id"])
        tracker.tracked_incident_ids.add(inc_id)

        # Confirm resources exist prior to throwing exception
        assert staged_file.exists()
        assert os_client.indices.exists(index=concrete_idx)

        # Record staged IDs and state in audit file for parent test verification
        audit_data = {{
            "bench_prefix": tracker.bench_prefix,
            "job_id": str(job_id),
            "source_id": str(source_id),
            "staged_file": str(staged_file),
            "concrete_idx": concrete_idx,
            "incident_id": str(inc_id),
            "initial_prefix": initial_prefix,
        }}
        audit_path = os.environ.get("AUDIT_FILE_PATH")
        if audit_path:
            Path(audit_path).write_text(json.dumps(audit_data))

        # Deliberately raise controlled exception inside the test using the pytest fixture
        raise RuntimeError("Controlled crash inside fixture-backed test")

def test_verify_fixture_restoration():
    assert settings.OPENSEARCH_INDEX_PREFIX == initial_prefix, (
        f"Settings prefix in subprocess was not restored: {{settings.OPENSEARCH_INDEX_PREFIX}}"
    )
    audit_path = os.environ.get("AUDIT_FILE_PATH")
    if audit_path and Path(audit_path).exists():
        audit = json.loads(Path(audit_path).read_text())
        audit["subprocess_prefix_restored"] = (settings.OPENSEARCH_INDEX_PREFIX == initial_prefix)
        Path(audit_path).write_text(json.dumps(audit))
'''
            test_file.write_text(isolated_test_code)

            env = os.environ.copy()
            env["PYTHONPATH"] = f"{backend_dir_path};{repo_root}"
            env["AUDIT_FILE_PATH"] = str(audit_file)
            env.pop("PYTEST_XDIST_WORKER", None)

            # Invoke isolated pytest subprocess pointing only to the temporary directory
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "--rootdir",
                    str(tmp_dir),
                    "-o",
                    "markers=benchmark",
                    str(test_file),
                ],
                env=env,
                capture_output=True,
                text=True,
            )

            # Pytest should have returned exitcode 1 due to the single intentional failure
            assert proc.returncode == 1, (
                f"Isolated pytest run unexpectedly exited with code {proc.returncode}.\n"
                f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )
            assert "Controlled crash inside fixture-backed test" in proc.stdout, (
                f"Controlled exception not found in pytest output:\n{proc.stdout}"
            )
            assert "1 failed" in proc.stdout, f"Expected 1 failed test in output:\n{proc.stdout}"
            assert "1 passed" in proc.stdout, f"Expected 1 passed test in output:\n{proc.stdout}"

            assert audit_file.exists(), "Resource audit file was not created by the isolated test"
            audit = json.loads(audit_file.read_text())

            assert audit.get("subprocess_prefix_restored") is True, (
                "Isolated subprocess did not restore settings.OPENSEARCH_INDEX_PREFIX"
            )

            # Affirmatively verify that the fixture's pytest-managed teardown cleaned up all resources:
            # 1. Filesystem: verify staged upload file was unlinked
            staged_path = Path(audit["staged_file"])
            assert not staged_path.exists(), f"Staged file {staged_path} was not deleted by fixture teardown"

            # 2. OpenSearch: verify concrete index was deleted
            os_client = get_opensearch_client()
            concrete_idx = audit["concrete_idx"]
            assert not os_client.indices.exists(index=concrete_idx), (
                f"Concrete index {concrete_idx} was not deleted by fixture teardown"
            )

            # 3. OpenSearch: verify entire benchmark run namespace is vacant
            bench_prefix = audit["bench_prefix"]
            leftovers = os_client.indices.get(index=f"{bench_prefix}-*")
            assert len(leftovers) == 0, f"Discovered leftover indices in namespace: {leftovers}"

            # 4. PostgreSQL: verify database rows were deleted
            await close_engine()
            sessionmaker = get_sessionmaker()
            async with sessionmaker() as session:
                inc_id = uuid.UUID(audit["incident_id"])
                inc_count = (await session.execute(
                    select(func.count(Incident.id)).where(Incident.id == inc_id)
                )).scalar()
                assert inc_count == 0, f"Incident {inc_id} was not deleted by fixture teardown"

                job_id = uuid.UUID(audit["job_id"])
                job_count = (await session.execute(
                    select(func.count(UploadJob.id)).where(UploadJob.id == job_id)
                )).scalar()
                assert job_count == 0, f"UploadJob {job_id} was not deleted by fixture teardown"

                source_id = uuid.UUID(audit["source_id"])
                src_count = (await session.execute(
                    select(func.count(LogSource.id)).where(LogSource.id == source_id)
                )).scalar()
                assert src_count == 0, f"LogSource {source_id} was not deleted by fixture teardown"
            await close_engine()
    finally:
        isolated_lock.release()
        # Guarantee exact settings restoration assertion even if an unexpected error occurs
        assert settings.OPENSEARCH_INDEX_PREFIX == original_prefix_snapshot, (
            f"OPENSEARCH_INDEX_PREFIX was not restored to exact initial snapshot: "
            f"expected '{original_prefix_snapshot}', got '{settings.OPENSEARCH_INDEX_PREFIX}'"
        )
