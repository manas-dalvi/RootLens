"""
Unit and integration tests for RootLens Stage 4C: Native HDFS Log Parser Integration.
Verifies:
1. Valid raw HDFS records parsing and field extraction.
2. Correct event-template mapping to canonical E1..E29 templates.
3. Correct chronological session construction by block ID.
4. Malformed HDFS records quarantine with explicit error reasons.
5. Valid normal HDFS sequences reaching LogBERT without false alarms.
6. Anomalous HDFS sequences generating sequence anomaly clusters.
7. Ordinary PostgreSQL / payment / API gateway logs bypassing LogBERT.
8. Existing microservice incident correlation remaining fully functional.
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import pytest

from app.core.config import settings
from app.normalizers import normalize_parsing_result
from app.parsers.factory import get_parser_for_format
from app.parsers.hdfs_parser import (
    HDFSLogParser,
    match_hdfs_template,
    map_hdfs_component_to_service,
)
from app.services.clustering_service import LogEventItem, cluster_log_events
from app.services.correlation_service import correlate_clusters
from app.services.logbert_service import (
    detect_sequence_anomalies_as_clusters,
    get_logbert_engine,
    group_logs_into_sessions,
    is_hdfs_domain_compatible,
)


@pytest.fixture(scope="module")
def hdfs_engine():
    """Retrieve the production full HDFS LogBERT inference engine."""
    engine = get_logbert_engine()
    assert engine is not None, "LogBERT engine could not be loaded"
    return engine


class TestHDFSParserIntegration:
    """Stage 4C test suite for native HDFS raw log parsing and LogBERT integration."""

    def test_1_valid_raw_hdfs_records(self):
        """Valid raw HDFS records are parsed and all canonical fields are extracted."""
        parser = get_parser_for_format("hdfs")
        assert isinstance(parser, HDFSLogParser)

        sample_lines = [
            "081109 203518 143 INFO dfs.DataNode$DataXceiver: Receiving block blk_-1608999687919862906 src: /10.250.19.102:54106 dest: /10.250.19.102:50010\n",
            "081109 203518 35 INFO dfs.FSNamesystem: BLOCK* NameSystem.allocateBlock: /mnt/hadoop/mapred/system/job_200811092030_0001/job.jar. blk_-1608999687919862906\n",
            "081109 203519 145 INFO dfs.DataNode$PacketResponder: PacketResponder 1 for block blk_-1608999687919862906 terminating\n",
        ]

        result = parser.parse_lines(sample_lines)
        assert result.format_type == "hdfs"
        assert result.total_records == 3
        assert result.successful_records == 3
        assert result.failed_records == 0

        rec0 = result.records[0]
        assert rec0.is_valid is True
        assert rec0.extracted["block_id"] == "blk_-1608999687919862906"
        assert rec0.extracted["event_id"] == "E5"
        assert rec0.extracted["service_name"] == "hdfs-datanode"
        assert rec0.extracted["log_level"] == "INFO"
        assert rec0.extracted["domain"] == "hdfs"
        assert rec0.extracted["dataset"] == "hdfs_benchmark"
        assert rec0.extracted["timestamp"] == "2008-11-09T20:35:18+00:00"

        rec1 = result.records[1]
        assert rec1.is_valid is True
        assert rec1.extracted["block_id"] == "blk_-1608999687919862906"
        assert rec1.extracted["event_id"] == "E22"
        assert rec1.extracted["service_name"] == "hdfs-namenode"

    def test_2_correct_event_template_mapping(self):
        """Messages map correctly to canonical benchmark event templates E1..E29."""
        test_cases = [
            ("Receiving block blk_123 src: /a:1 dest: /b:2", "E5"),
            ("Received block blk_123 src: /a:1 dest: /b:2 of size 100", "E6"),
            ("PacketResponder 0 for block blk_123 terminating", "E11"),
            ("Received block blk_123 of size 91178 from /10.250.10.6", "E9"),
            ("BLOCK* NameSystem.allocateBlock: /path/file. blk_123", "E22"),
            ("BLOCK* NameSystem.addStoredBlock: blockMap updated: 10.1:50010 is added to blk_123 size 91178", "E26"),
            ("BLOCK* NameSystem.delete: blk_123 is added to invalidSet of 10.1:50010", "E23"),
            ("Deleting block blk_123 file /data/blk_123", "E21"),
            ("Verification succeeded for blk_123", "E2"),
        ]

        for msg, expected_event in test_cases:
            matched = match_hdfs_template(msg)
            assert matched == expected_event, f"Expected {expected_event} for '{msg}', got {matched}"

    def test_3_correct_chronological_session_construction(self):
        """Parsed HDFS records are grouped into sessions by block_id and chronologically ordered."""
        parser = HDFSLogParser()
        lines = [
            "081109 203519 145 INFO dfs.DataNode$PacketResponder: PacketResponder 1 for block blk_1001 terminating\n",
            "081109 203518 143 INFO dfs.DataNode$DataXceiver: Receiving block blk_1001 src: /10.1:1 dest: /10.2:2\n",
            "081109 203518 35 INFO dfs.FSNamesystem: BLOCK* NameSystem.allocateBlock: /job.jar. blk_1002\n",
        ]

        parse_res = parser.parse_lines(lines)
        norm_res = normalize_parsing_result(parse_res, default_service="hdfs", default_env="production")
        assert norm_res.valid_records == 3

        # Convert to LogEventItem
        log_items = [
            LogEventItem(
                log_id=f"log_{i}",
                timestamp=r.timestamp,
                service_name=r.service_name,
                log_level=r.log_level or "INFO",
                message=r.message,
                attributes=r.attributes,
                raw_log=r.raw_log,
            )
            for i, r in enumerate(norm_res.records)
        ]

        sessions = group_logs_into_sessions(log_items, window_minutes=15)
        assert "blk_1001" in sessions
        assert "blk_1002" in sessions
        assert len(sessions["blk_1001"]) == 2
        assert len(sessions["blk_1002"]) == 1

        # Check chronological ordering: line at 203518 must precede line at 203519
        s1001 = sessions["blk_1001"]
        assert s1001[0].attributes["event_id"] == "E5"
        assert s1001[1].attributes["event_id"] == "E11"
        assert s1001[0].timestamp <= s1001[1].timestamp

    def test_4_malformed_hdfs_records_and_quarantine(self):
        """Malformed records are safely quarantined with clear error reasons."""
        parser = HDFSLogParser()
        lines = [
            # 1. Invalid header structure
            "Corrupted log line without date and time header\n",
            # 2. Invalid date digits
            "999999 999999 143 INFO dfs.DataNode$DataXceiver: Receiving block blk_999 src: /a dest: /b\n",
            # 3. Missing block ID
            "081109 203518 143 INFO dfs.DataNode$DataXceiver: Receiving block without identifier\n",
            # 4. Unmapped random text message
            "081109 203518 143 INFO dfs.DataNode$DataXceiver: Random unrecognized text on blk_999\n",
        ]

        result = parser.parse_lines(lines)
        assert result.successful_records == 0
        assert result.failed_records == 4
        assert len(result.records) == 4

        assert "does not match benchmark timestamp/component header structure" in result.records[0].error
        assert "invalid date/time digits" in result.records[1].error
        assert "missing required block ID" in result.records[2].error
        assert "did not match any canonical HDFS event template" in result.records[3].error

    def test_5_valid_hdfs_sequences_reaching_logbert(self, hdfs_engine):
        """Valid normal HDFS sequences reach LogBERT, evaluate below 0.0004, and yield 0 clusters."""
        # Verified normal benchmark session blk_410678498721295872 (19 events)
        norm_events = [
            "E5", "E5", "E5", "E22", "E11", "E9", "E11", "E9", "E11", "E9",
            "E26", "E26", "E26", "E23", "E23", "E23", "E21", "E21", "E21",
        ]
        base_time = datetime(2008, 11, 9, 20, 35, 18, tzinfo=timezone.utc)

        log_items = [
            LogEventItem(
                log_id=f"norm_{i}",
                timestamp=base_time + timedelta(seconds=i),
                service_name="hdfs-datanode" if e != "E22" else "hdfs-namenode",
                log_level="INFO",
                message=f"HDFS log event for blk_410678498721295872 {e}",
                attributes={
                    "block_id": "blk_410678498721295872",
                    "event_id": e,
                    "template": e,
                    "domain": "hdfs",
                    "dataset": "hdfs_benchmark",
                },
            )
            for i, e in enumerate(norm_events)
        ]

        # Verify domain compatibility
        is_comp, reason, tokens = is_hdfs_domain_compatible(
            session_id="blk_410678498721295872",
            session_events=log_items,
            engine=hdfs_engine,
        )
        assert is_comp is True
        assert reason == "COMPATIBLE_HDFS"
        assert tokens == norm_events

        # Inference evaluation
        clusters = detect_sequence_anomalies_as_clusters(log_items, window_minutes=15, engine=hdfs_engine)
        assert len(clusters) == 0, "Normal sequence must NOT generate anomaly clusters"
        assert log_items[0].attributes["logbert_status"] == "NORMAL"
        assert log_items[0].attributes["anomaly_score"] < 0.0004

    def test_6_anomalous_hdfs_sequences_generating_sequence_anomaly_clusters(self, hdfs_engine):
        """Anomalous HDFS sequences evaluate above 0.0004 and produce SEQUENCE_ANOMALY cluster."""
        # Verified anomaly benchmark session blk_6492489344990292445
        anom_events = [
            "E5", "E22", "E5", "E5", "E11", "E9", "E11", "E9", "E11", "E9",
            "E26", "E26", "E26", "E11", "E9", "E26", "E11", "E9", "E26", "E11",
            "E9", "E26", "E11", "E9", "E26", "E11", "E9", "E26", "E11", "E9",
            "E26", "E11", "E9", "E26", "E11", "E9", "E26", "E26", "E26",
        ]
        base_time = datetime(2008, 11, 9, 21, 0, 0, tzinfo=timezone.utc)

        log_items = [
            LogEventItem(
                log_id=f"anom_{i}",
                timestamp=base_time + timedelta(seconds=i),
                service_name="hdfs-datanode" if e != "E22" else "hdfs-namenode",
                log_level="INFO",
                message=f"HDFS block blk_6492489344990292445 action {e}",
                attributes={
                    "block_id": "blk_6492489344990292445",
                    "event_id": e,
                    "template": e,
                    "domain": "hdfs",
                    "dataset": "hdfs_benchmark",
                },
            )
            for i, e in enumerate(anom_events)
        ]

        clusters = detect_sequence_anomalies_as_clusters(log_items, window_minutes=15, engine=hdfs_engine)
        assert len(clusters) == 1
        cl = clusters[0]
        assert "cl-logbert-blk_649248934499" in cl.cluster_id
        assert "LogBERT Sequence Anomaly" in cl.pattern
        assert log_items[0].attributes["logbert_status"] == "ANOMALOUS"
        assert log_items[0].attributes["anomaly_score"] >= 0.0004
        assert log_items[0].attributes["threshold"] == 0.0004

    def test_7_ordinary_microservice_logs_bypass_logbert(self, hdfs_engine):
        """Microservice logs bypass HDFS parser heuristic and LogBERT domain guard."""
        microservice_lines = [
            "2026-10-10 19:00:00.123 INFO [api-gateway] Request received for /checkout\n",
            "2026-10-10 19:01:15.456 ERROR [payment-service] Payment gateway timeout\n",
            "2026-10-10 19:02:00.789 FATAL [postgres-db] remaining connection slots are reserved\n",
        ]

        # 1. HDFSLogParser.is_hdfs_format must be False
        assert HDFSLogParser.is_hdfs_format(microservice_lines) is False

        # 2. Parsing via HDFS parser marks lines as invalid/malformed
        hdfs_res = HDFSLogParser().parse_lines(microservice_lines)
        assert hdfs_res.successful_records == 0
        assert hdfs_res.failed_records == 3

        # 3. LogBERT domain guard rejects microservice session items
        now = datetime.now(timezone.utc)
        ms_logs = [
            LogEventItem(
                log_id="ms_001",
                timestamp=now,
                service_name="payment-service",
                log_level="ERROR",
                message="Payment gateway timeout after 30000ms",
                attributes={},
            )
        ]
        is_comp, reason, _ = is_hdfs_domain_compatible("ms_sess", ms_logs, engine=hdfs_engine)
        assert is_comp is False
        assert "BYPASSED_NON_HDFS_MICROSERVICE" in reason

        clusters = detect_sequence_anomalies_as_clusters(ms_logs, window_minutes=15, engine=hdfs_engine)
        assert len(clusters) == 0

    def test_8_existing_microservice_incident_correlation_remains_functional(self, hdfs_engine):
        """Existing microservice cascade dataset continues to correlate and preserves safeguards."""
        sample_path = Path("data/samples/scenario_db_cascade.json")
        assert sample_path.exists()

        with open(sample_path, "r", encoding="utf-8") as f:
            raw_docs = json.load(f)

        log_items = [
            LogEventItem(
                log_id=d.get("log_id") or d.get("id") or f"doc_{i}",
                timestamp=datetime.fromisoformat(d["timestamp"]),
                service_name=d["service_name"],
                log_level=d.get("log_level") or d.get("severity") or "INFO",
                message=d["message"],
                attributes=d.get("attributes", {}),
                trace_id=d.get("trace_id"),
                span_id=d.get("span_id"),
            )
            for i, d in enumerate(raw_docs)
        ]

        # 1. Microservice logs bypass LogBERT
        logbert_clusters = detect_sequence_anomalies_as_clusters(log_items, window_minutes=15, engine=hdfs_engine)
        assert len(logbert_clusters) == 0

        # 2. Standard pattern clustering functions
        pattern_clusters = cluster_log_events(log_items, window_minutes=15)
        assert len(pattern_clusters) > 0

        # 3. Multi-service cascade correlation forms candidates
        candidates = correlate_clusters(pattern_clusters, cascade_window_seconds=120)
        assert len(candidates) > 0

        top_cand = candidates[0]
        assert "postgres-db" in [s.service_name for s in top_cand.affected_services]
        # Invariant preserved
        assert top_cand.is_root_cause_confirmed is False
