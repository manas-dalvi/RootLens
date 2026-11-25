# RootLens RCA Ground Truth Benchmark — Evaluation Report

- **Evaluated At (UTC)**: `2026-11-24T17:34:42.269379+00:00`
- **Dataset Type**: `SYNTHETIC_EVALUATION_FIXTURE`
- **Version**: `1.0.0`
- **Total Scenarios Evaluated**: `12 / 12`

## 1. Measured Metric Summary

| Metric | Rate / Value | Numerator / Denominator | Sample Count | Definition |
|---|:---:|:---:|:---:|---|
| **Top-1 Accuracy (Classifiable)** | **100.0%** | 11 / 11 | 11 | Primary finding correctly identifies culprit service and failure mechanism (over classifiable incidents) |
| **Top-3 Accuracy (Classifiable)** | **100.0%** | 11 / 11 | 11 | Expected culprit service and mechanism present in top-3 findings (over classifiable incidents) |
| **Culprit Service Accuracy (Classifiable)** | **100.0%** | 11 / 11 | 11 | Initiating failure service correctly identified (over classifiable incidents) |
| **Precision@K** | **86.4%** | 9.5 / 11 | 11 | Fraction of retrieved evidence in top-K that is ground-truth relevant |
| **Recall@K** | **100.0%** | 11.0 / 11 | 11 | Fraction of ground-truth relevant evidence retrieved |
| **Mean Reciprocal Rank (MRR)** | **89.4%** | 9.8333 / 11 | 11 | Reciprocal rank of the first relevant failure evidence item |
| **Citation Validity Rate** | **100.0%** | 84 / 84 | 12 | Citations confirmed to resolve against incident evidence |
| **Unsupported Factual Claim Rate** | **50.8%** | 32 / 63 | 12 | Factual claims lacking verified telemetry citations |
| **Correct Abstention Rate (Insufficient Telemetry)** | **100.0%** | 1 / 1 | 1 | Correct abstention from root cause attribution on sparse evidence |
| **Incorrect Abstention Rate (Classifiable Incidents)** | **0.0%** | 0 / 11 | 11 | Incorrect abstention when sufficient telemetry was available (false negative) |
| **False Root Cause Attribution Rate** | **0.0%** | 0 / 1 | 1 | Attempted root cause attribution on insufficient telemetry (false positive) |
| **False Confirmation Rate** | **0.0%** | 0 / 12 | 12 | Automatic promotion of unreviewed hypothesis to confirmed root cause |
| **False Causal Attribution Rate** | **0.0%** | 0 / 12 | 12 | Attribution of confirmed causality from temporal proximity alone |

## 2. Per-Scenario Evaluation Breakdown

| ID | Scenario Name | Classification | Culprit Match | Top-1 | Top-3 | Prec@K | Rec@K | Notes |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|---|
| `RCA-SCN-01` | Single Service Database Connection Pool Exhaustion | `TRACE_LINKED` | MATCH | PASS | PASS | 0.50 | 1.00 | Classification: TRACE_LINKED (expected: TRACE_LINKED). Culprit: 'postgres-db' (expected: 'postgres-db'). Evidence count: 3. |
| `RCA-SCN-02` | Auth Key Rotation Failure with Multiple Corroborating Logs | `TRACE_LINKED` | MATCH | PASS | PASS | 1.00 | 1.00 | Classification: TRACE_LINKED (expected: TRACE_LINKED). Culprit: 'auth-service' (expected: 'auth-service'). Evidence count: 4. |
| `RCA-SCN-03` | Multiple Competing Hypotheses with One True Root Cause | `TRACE_LINKED` | MATCH | PASS | PASS | 1.00 | 1.00 | Classification: TRACE_LINKED (expected: TRACE_LINKED). Culprit: 'inventory-service' (expected: 'inventory-service'). Evidence count: 3. |
| `RCA-SCN-04` | Unrelated Background Logs Present During Incident | `TEMPORAL_ONLY` | MATCH | PASS | PASS | 0.00 | 1.00 | Classification: TEMPORAL_ONLY (expected: TRACE_LINKED). Culprit: 'billing-service' (expected: 'billing-service'). Evidence count: 4. |
| `RCA-SCN-05` | Missing Trace Context Across Services | `TEMPORAL_ONLY` | MATCH | PASS | PASS | 1.00 | 1.00 | Classification: TEMPORAL_ONLY (expected: TEMPORAL_ONLY). Culprit: 'checkout-service' (expected: 'checkout-service'). Evidence count: 2. |
| `RCA-SCN-06` | Missing or Incomplete Telemetry Identifiers | `TEMPORAL_ONLY` | MATCH | PASS | PASS | 1.00 | 1.00 | Classification: TEMPORAL_ONLY (expected: TEMPORAL_ONLY). Culprit: 'unassigned' (expected: 'unassigned'). Evidence count: 2. |
| `RCA-SCN-07` | Insufficient Evidence to Determine Root Cause | `INSUFFICIENT` | N/A (Abstain) | N/A (Abstain) | N/A (Abstain) | N/A | N/A | Classification: INSUFFICIENT (expected: INSUFFICIENT). Culprit: 'None' (expected: 'None'). Evidence count: 1. |
| `RCA-SCN-08` | Fabricated or Non-Existent Evidence References | `TRACE_LINKED` | MATCH | PASS | PASS | 1.00 | 1.00 | Classification: TRACE_LINKED (expected: TRACE_LINKED). Culprit: 'auth-service' (expected: 'auth-service'). Evidence count: 2. |
| `RCA-SCN-09` | Cross-Incident Evidence References | `TRACE_LINKED` | MATCH | PASS | PASS | 1.00 | 1.00 | Classification: TRACE_LINKED (expected: TRACE_LINKED). Culprit: 'payment-service' (expected: 'payment-service'). Evidence count: 2. |
| `RCA-SCN-10` | Evidence Associated with the Wrong Service | `TRACE_LINKED` | MATCH | PASS | PASS | 1.00 | 1.00 | Classification: TRACE_LINKED (expected: TRACE_LINKED). Culprit: 'redis-cache' (expected: 'redis-cache'). Evidence count: 2. |
| `RCA-SCN-11` | Multiple Concurrent Failures with Disjoint Traces | `TRACE_LINKED` | MATCH | PASS | PASS | 1.00 | 1.00 | Classification: TRACE_LINKED (expected: TRACE_LINKED). Culprit: 'search-indexer' (expected: 'search-indexer'). Evidence count: 2. |
| `RCA-SCN-12` | Temporal Proximity Without Causal Proof | `TEMPORAL_ONLY` | MATCH | PASS | PASS | 1.00 | 1.00 | Classification: TEMPORAL_ONLY (expected: TEMPORAL_ONLY). Culprit: 'recommender-engine' (expected: 'recommender-engine'). Evidence count: 2. |

## 3. Dataset Integrity & Limitations Disclosure

> [!IMPORTANT]
> **Benchmark Limitations Disclosure**:
> 1. **Synthetic Fixture**: This evaluation benchmark consists of 12 hand-curated synthetic microservice failure scenarios. It evaluates algorithmic logic, boundary safety, and citation verification, but does **not** claim statistical generalization across arbitrary production deployments.
> 2. **Distinction from Real-World Anomaly Benchmarks**: These results evaluate multi-service incident root cause synthesis and must not be conflated with the HDFS held-out log anomaly benchmark (LogBERT F1: 0.6802, Precision: 0.9745, Recall: 0.5224, Threshold: 0.0004).
> 3. **Non-Invention Guarantee**: Every metric above is reported with its explicit numerator, denominator, and sample count.
