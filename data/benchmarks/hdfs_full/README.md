# Full HDFS Benchmark Dataset (HDFS_v1) for LogBERT

## 1. Provenance & Attribution

- **Dataset**: Hadoop Distributed File System (HDFS) Console Log Benchmark (`HDFS_v1`)
- **Original Research**: Wei Xu, Ling Huang, Armando Fox, David Patterson, Michael I. Jordan. *"Detecting Large-Scale System Problems by Mining Console Logs."* SOSP '09: Proceedings of the ACM SIGOPS 22nd Symposium on Operating Systems Principles, 2009.
- **Dataset Curator & Host**: LogPAI Team (Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu, Michael R. Lyu). *"Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics."* IEEE International Symposium on Software Reliability Engineering (ISSRE), 2023.
- **Official Zenodo Record**: [DOI: 10.5281/zenodo.8196385](https://doi.org/10.5281/zenodo.8196385), Record ID: `8196385`
- **Source Archive**: `HDFS_v1.zip`
- **Verified MD5 Checksum**: `76a24b4d9a6164d543fb275f89773260`
- **Download Size**: `186,645,559 bytes` (~178.0 MB)
- **Dataset License**: **Creative Commons Attribution 4.0 International (CC-BY 4.0)** (Zenodo Record 8196385)
- **Tooling & Parser License**: **MIT License** (LogPAI `loglizer` / `logparser` toolkit, Copyright 2018 LogPAI)
- **Citation Requirements**: Free for research and academic use, with mandatory citation of the SOSP 2009 and ISSRE 2023 papers.

## 2. Dataset Verification Summary

- **Total Raw Lines (`HDFS.log`)**: 11,175,629
- **Total Unique Block IDs**: 575,061
- **Ground Truth Labels (`anomaly_label.csv`)**: 575,061
  - **Normal**: 558,223 (97.07%)
  - **Anomaly**: 16,838 (2.93%)
- **Unlabeled / Unmatched Block IDs**: 0 (100% matched across raw logs, traces, and labels)
- **Duplicate Records**: 0
- **Parser Coverage**: 100.0% across canonical templates `E1`–`E29`

## 3. Sequence Length Distribution

Across all 575,061 sessions:
- **Min Length**: 2
- **Max Length**: 298
- **Mean Length**: 19.43
- **Median Length**: 19
- **p25 / p50 / p75**: 19 / 19 / 20
- **p90 / p95 / p99**: 25 / 28 / 33

### Bins:
- **Length = 1**: 0 (0.00%)
- **Length 2–5**: 6,181 (1.07%)
- **Length 6–10**: 10 (0.00%)
- **Length 11–20**: 427,700 (74.37%)
- **Length > 20**: 141,170 (24.55%)

## 4. Partitions & Zero-Leakage Policy

- **Train Split (`train_sessions.json`)**: 446,578 sessions (100% Normal, 0 Anomalies)
- **Validation Split (`val_sessions.json`)**: 64,241 sessions (55,822 Normal, 8,419 Anomaly)
- **Test Split (`test_sessions.json`)**: 64,242 sessions (55,823 Normal, 8,419 Anomaly)
- **Disjointness**: 100% zero block ID overlap across all three splits.

### Curated Subsets for CPU Development:
- `train_sessions_sampled.json`: 4,855 sessions (100% Normal, matching LogBERT paper benchmark setting)
- `val_sessions_sampled.json`: 2,000 sessions (1,800 Normal, 200 Anomaly)
- `test_sessions_sampled.json`: 2,000 sessions (1,800 Normal, 200 Anomaly)

## 5. Vocabulary Policy

- **Training Vocabulary (`vocab.json`)**: 22 tokens (5 special tokens + 17 normal event templates `E2, E3, E4, E5, E6, E9, E11, E16, E18, E20, E21, E22, E23, E25, E26, E27, E28`).
- **Canonical Vocabulary (`vocab_canonical.json`)**: 34 tokens (5 special tokens + 29 templates `E1`–`E29`).
- **OOV Rate**:
  - Validation: 0.2224% (2,741 / 1,232,302 tokens)
  - Test: 0.2128% (2,625 / 1,233,324 tokens)
  - All 12 OOV templates (`E1, E7, E8, E10, E12, E13, E14, E15, E17, E19, E24, E29`) are exclusively error/exception events that never occur during normal operations, correctly mapping to `[UNK]` and triggering high anomaly scores.
