"""
RootLens Stage 3B: LogBERT Domain Compatibility Guard Test Suite.

Verifies that the HDFS-trained LogBERT model only evaluates inputs belonging
to its supported HDFS event domain, while ordinary enterprise microservice logs
(postgres-db, payment-service, api-gateway, order-service) gracefully bypass
inference without generating false-positive anomaly clusters.
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import pytest

from app.core.config import settings
from app.services.clustering_service import LogEventItem, cluster_log_events
from app.services.correlation_service import correlate_clusters
from app.services.logbert_service import (
    detect_sequence_anomalies_as_clusters,
    get_logbert_engine,
    is_hdfs_domain_compatible,
)


@pytest.fixture(scope="module")
def hdfs_engine():
    """Retrieve the active LogBERT inference engine."""
    engine = get_logbert_engine()
    assert engine is not None, "LogBERT engine must be available for testing"
    return engine


class TestLogBERTDomainCompatibilityGuard:
    """Validate domain compatibility guard enforcement and microservice bypass."""

    def test_valid_hdfs_sequence_reaches_logbert(self, hdfs_engine):
        """Valid HDFS block sequence with canonical event tokens reaches LogBERT and is evaluated."""
        now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
        
        # Production normal HDFS verification sample
        normal_events = [
            "E5", "E5", "E5", "E22", "E11", "E9", "E11", "E9", "E11", "E9",
            "E26", "E26", "E26", "E23", "E23", "E23", "E21", "E21", "E21"
        ]
        logs_normal = [
            LogEventItem(
                log_id=f"hdfs_norm_{i}",
                timestamp=now + timedelta(seconds=i),
                service_name="hdfs-datanode",
                log_level="INFO",
                message=f"HDFS block blk_410678498721295872 action {tok}",
                attributes={"block_id": "blk_410678498721295872", "template": tok},
                raw_log=f"raw hdfs {tok}",
            )
            for i, tok in enumerate(normal_events)
        ]

        is_comp, reason, tokens = is_hdfs_domain_compatible(
            session_id="blk_410678498721295872",
            session_events=logs_normal,
            engine=hdfs_engine,
        )
        assert is_comp is True
        assert reason == "COMPATIBLE_HDFS"
        assert len(tokens) == 19

        # Evaluate through clustering pipeline
        clusters = detect_sequence_anomalies_as_clusters(logs_normal, window_minutes=15, engine=hdfs_engine)
        # Normal sequence score is 0.0003 < 0.0004 -> No anomaly cluster generated
        assert len(clusters) == 0
        assert logs_normal[0].attributes.get("logbert_status") == "NORMAL"
        assert logs_normal[0].attributes.get("model") == "LogBERT"

        # Production anomaly HDFS verification sample
        anomaly_events = [
            "E5", "E5", "E22", "E5", "E26", "E11", "E9", "E11", "E9", "E11", "E9",
            "E26", "E26", "E2", "E3", "E3", "E4", "E25", "E18", "E5", "E6", "E16",
            "E26", "E26", "E21", "E25", "E5", "E18", "E6", "E16", "E26", "E26",
            "E21", "E23", "E23", "E23", "E21", "E21", "E21"
        ]
        logs_anom = [
            LogEventItem(
                log_id=f"hdfs_anom_{i}",
                timestamp=now + timedelta(seconds=i),
                service_name="hdfs-datanode",
                log_level="INFO",
                message=f"HDFS block blk_6492489344990292445 action {tok}",
                attributes={"block_id": "blk_6492489344990292445", "template": tok},
                raw_log=f"raw hdfs anom {tok}",
            )
            for i, tok in enumerate(anomaly_events)
        ]

        clusters_anom = detect_sequence_anomalies_as_clusters(logs_anom, window_minutes=15, engine=hdfs_engine)
        assert len(clusters_anom) == 1
        cl = clusters_anom[0]
        assert "cl-logbert-" in cl.cluster_id
        assert cl.events[0].attributes.get("logbert_status") == "ANOMALOUS"
        assert cl.events[0].attributes.get("anomaly_score") == pytest.approx(0.1288, abs=1e-3)

    def test_ordinary_postgresql_messages_bypass_hdfs_inference(self, hdfs_engine):
        """Ordinary PostgreSQL messages strictly bypass HDFS LogBERT inference."""
        now = datetime(2026, 10, 14, 8, 1, 0, tzinfo=timezone.utc)
        pg_logs = [
            LogEventItem(
                log_id="pg_01",
                timestamp=now,
                service_name="postgres-db",
                log_level="INFO",
                message="database system is ready to accept connections; max_connections=100",
                raw_log="2026-10-14 INFO database system is ready to accept connections; max_connections=100",
            ),
            LogEventItem(
                log_id="pg_02",
                timestamp=now + timedelta(seconds=5),
                service_name="postgres-db",
                log_level="WARN",
                message="connection pool approaching capacity: active=88 max=100 idle=2",
                raw_log="2026-10-14 WARN connection pool approaching capacity: active=88 max=100 idle=2",
            ),
            LogEventItem(
                log_id="pg_03",
                timestamp=now + timedelta(seconds=10),
                service_name="postgres-db",
                log_level="FATAL",
                message="FATAL: remaining connection slots are reserved for non-replication superuser connections",
                raw_log="2026-10-14 FATAL remaining connection slots are reserved",
            ),
        ]

        is_comp, reason, _ = is_hdfs_domain_compatible(
            session_id="postgres-db_202610140801",
            session_events=pg_logs,
            engine=hdfs_engine,
        )
        assert is_comp is False
        assert "BYPASSED_NON_HDFS_MICROSERVICE" in reason

        # Pipeline test
        clusters = detect_sequence_anomalies_as_clusters(pg_logs, window_minutes=15, engine=hdfs_engine)
        assert len(clusters) == 0
        assert pg_logs[0].attributes.get("logbert_status") == "BYPASSED"
        assert "BYPASSED_NON_HDFS_MICROSERVICE" in pg_logs[0].attributes.get("logbert_bypass_reason", "")
        assert pg_logs[0].attributes.get("model") != "LogBERT"
        assert "anomaly_score" not in pg_logs[0].attributes

    def test_e_number_in_postgres_message_does_not_grant_hdfs_compatibility(self, hdfs_engine):
        """Presence of an E-number (e.g. E5) in a PostgreSQL message does not grant HDFS compatibility."""
        now = datetime(2026, 10, 14, 8, 1, 0, tzinfo=timezone.utc)
        pg_tricky_logs = [
            LogEventItem(
                log_id="pg_e5_01",
                timestamp=now,
                service_name="postgres-db",
                log_level="ERROR",
                message="PostgreSQL execution error code E5 occurred on query execution",
                attributes={"event_id": "E5"},
                raw_log="PostgreSQL error E5",
            ),
        ]

        is_comp, reason, _ = is_hdfs_domain_compatible(
            session_id="postgres-db_202610140801",
            session_events=pg_tricky_logs,
            engine=hdfs_engine,
        )
        assert is_comp is False
        assert "BYPASSED_NON_HDFS_MICROSERVICE" in reason

        clusters = detect_sequence_anomalies_as_clusters(pg_tricky_logs, window_minutes=15, engine=hdfs_engine)
        assert len(clusters) == 0
        assert pg_tricky_logs[0].attributes.get("logbert_status") == "BYPASSED"

    def test_payment_service_and_api_gateway_bypass_hdfs_inference(self, hdfs_engine):
        """Payment service and API Gateway logs bypass HDFS LogBERT inference."""
        now = datetime(2026, 10, 14, 8, 0, 0, tzinfo=timezone.utc)

        gw_logs = [
            LogEventItem(
                log_id="gw_01",
                timestamp=now,
                service_name="api-gateway",
                log_level="INFO",
                message="Gateway routing engine healthy; active downstream targets: 4",
                trace_id="trace-gw-01",
            )
        ]
        pay_logs = [
            LogEventItem(
                log_id="pay_01",
                timestamp=now + timedelta(seconds=10),
                service_name="payment-service",
                log_level="INFO",
                message="Payment processing worker pool active; queue consumer running",
                trace_id="trace-pay-01",
            )
        ]

        # Gateway check
        is_comp_gw, reason_gw, _ = is_hdfs_domain_compatible("gw_sess", gw_logs, engine=hdfs_engine)
        assert is_comp_gw is False
        assert "BYPASSED_NON_HDFS_MICROSERVICE" in reason_gw
        gw_clusters = detect_sequence_anomalies_as_clusters(gw_logs, window_minutes=15, engine=hdfs_engine)
        assert len(gw_clusters) == 0
        assert gw_logs[0].attributes.get("logbert_status") == "BYPASSED"

        # Payment check
        is_comp_pay, reason_pay, _ = is_hdfs_domain_compatible("pay_sess", pay_logs, engine=hdfs_engine)
        assert is_comp_pay is False
        assert "BYPASSED_NON_HDFS_MICROSERVICE" in reason_pay
        pay_clusters = detect_sequence_anomalies_as_clusters(pay_logs, window_minutes=15, engine=hdfs_engine)
        assert len(pay_clusters) == 0
        assert pay_logs[0].attributes.get("logbert_status") == "BYPASSED"

    def test_unknown_tokens_do_not_generate_false_logbert_anomaly_clusters(self, hdfs_engine):
        """Healthy microservice logs (which mapped to UNK) no longer generate false-positive anomaly clusters."""
        now = datetime(2026, 10, 14, 8, 0, 0, tzinfo=timezone.utc)
        healthy_startup_logs = [
            LogEventItem(
                log_id="start_01",
                timestamp=now,
                service_name="api-gateway",
                log_level="INFO",
                message="Gateway routing engine healthy; active downstream targets: 4",
            ),
            LogEventItem(
                log_id="start_02",
                timestamp=now + timedelta(seconds=5),
                service_name="order-service",
                log_level="INFO",
                message="Order service listening on port 8080; connection pool initialized",
            ),
            LogEventItem(
                log_id="start_03",
                timestamp=now + timedelta(seconds=10),
                service_name="payment-service",
                log_level="INFO",
                message="Payment processing worker pool active; queue consumer running",
            ),
            LogEventItem(
                log_id="start_04",
                timestamp=now + timedelta(seconds=15),
                service_name="postgres-db",
                log_level="INFO",
                message="database system is ready to accept connections; max_connections=100",
            ),
        ]

        clusters = detect_sequence_anomalies_as_clusters(healthy_startup_logs, window_minutes=15, engine=hdfs_engine)
        # MUST BE ZERO clusters (false positive completely eliminated)
        assert len(clusters) == 0
        for log in healthy_startup_logs:
            assert log.attributes.get("logbert_status") == "BYPASSED"

    def test_existing_hdfs_benchmark_inference_remains_unchanged(self, hdfs_engine):
        """Existing HDFS benchmark single-sequence inference and thresholds remain strictly invariant."""
        # Verified normal sample blk_410678498721295872
        normal_seq = [
            "E5", "E5", "E5", "E22", "E11", "E9", "E11", "E9", "E11", "E9",
            "E26", "E26", "E26", "E23", "E23", "E23", "E21", "E21", "E21"
        ]
        res_norm = hdfs_engine.predict_sequence(normal_seq, session_id="blk_410678498721295872")
        assert res_norm.is_anomaly is False
        assert res_norm.composite_score == pytest.approx(0.0003, abs=1e-4)
        assert res_norm.threshold == pytest.approx(0.0004, abs=1e-5)
        assert res_norm.mismatch_count == 0

        # Verified anomaly sample blk_6492489344990292445
        anom_seq = [
            "E5", "E5", "E22", "E5", "E26", "E11", "E9", "E11", "E9", "E11", "E9",
            "E26", "E26", "E2", "E3", "E3", "E4", "E25", "E18", "E5", "E6", "E16",
            "E26", "E26", "E21", "E25", "E5", "E18", "E6", "E16", "E26", "E26",
            "E21", "E23", "E23", "E23", "E21", "E21", "E21"
        ]
        res_anom = hdfs_engine.predict_sequence(anom_seq, session_id="blk_6492489344990292445")
        assert res_anom.is_anomaly is True
        assert res_anom.composite_score == pytest.approx(0.1288, abs=1e-3)
        assert res_anom.threshold == pytest.approx(0.0004, abs=1e-5)
        assert res_anom.mismatch_count == 5

    def test_bypassed_logs_remain_available_to_normal_incident_pipeline(self, hdfs_engine):
        """Bypassed microservice logs remain fully accessible to standard pattern clustering and RCA correlation."""
        cascade_file = Path("data/samples/scenario_db_cascade.json")
        assert cascade_file.exists()
        with open(cascade_file, "r", encoding="utf-8") as f:
            logs_raw = json.load(f)

        all_logs: list[LogEventItem] = []
        for idx, item in enumerate(logs_raw):
            ts = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
            all_logs.append(LogEventItem(
                log_id=f"cascade_log_{idx:03d}",
                timestamp=ts,
                service_name=item.get("service_name", "default"),
                environment=item.get("environment", "production"),
                log_level=item.get("log_level", "INFO"),
                message=item.get("message", ""),
                trace_id=item.get("trace_id"),
                attributes=item,
                raw_log=json.dumps(item),
            ))

        # 1. LogBERT evaluation: all microservice sessions should bypass
        anomaly_clusters = detect_sequence_anomalies_as_clusters(all_logs, window_minutes=15, engine=hdfs_engine)
        assert len(anomaly_clusters) == 0, "No fake LogBERT sequence anomaly clusters should be created for microservices"

        # 2. Standard pattern clustering: errors MUST still be clustered
        pattern_clusters = cluster_log_events(all_logs, window_minutes=15)
        assert len(pattern_clusters) > 0, "Real error logs must form standard pattern clusters"

        # 3. Incident correlation: should correctly assemble the cascade candidate
        candidates = correlate_clusters(pattern_clusters)
        assert len(candidates) >= 1
        top_cand = candidates[0]

        # Invariant checks:
        assert top_cand.is_root_cause_confirmed is False
        assert "postgres-db" in top_cand.title or any(ev.service == "postgres-db" for ev in top_cand.affected_services)
        assert len(top_cand.timeline_events) > 0
        assert len(top_cand.evidence_items) > 0
