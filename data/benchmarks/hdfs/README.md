# HDFS Anomaly Detection Benchmark Dataset

## Provenance & Attribution

- **Dataset**: Hadoop Distributed File System (HDFS) Log Dataset (`HDFS_v1` / `HDFS_2k` benchmark slice)
- **Original Research**: Wei Xu, Ling Huang, Armando Fox, David Patterson, Michael I. Jordan. *"Detecting Large-Scale System Problems by Mining Console Logs."* SOSP '09: Proceedings of the ACM SIGOPS 22nd Symposium on Operating Systems Principles, 2009.
- **Dataset Curator & Host**: LogPAI Team (Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu, Michael R. Lyu). *"Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics."* ISSRE 2023 / ICSE 2019. Repository: `https://github.com/logpai/loghub`.
- **Dataset License**: **Loghub Research and Academic Usage License**: The dataset is freely available for research and academic work under the condition of citing the Loghub paper and referencing `https://github.com/logpai/loghub`.
- **Tooling & Baseline Toolkit License**: **MIT License** (Copyright 2018 LogPAI, `https://github.com/logpai/loglizer`).

## Dataset Description

The HDFS dataset was generated in a private cloud environment using Hadoop MapReduce workloads running across more than 200 Amazon EC2 nodes.

1. **Log Volume**:
   - The full HDFS v1 dataset contains 11,175,629 raw log messages sliced into 575,061 session blocks based on unique block identifiers (`blk_<id>`).
   - The curated `HDFS_2k` benchmark slice contains 2,000 parsed log entries spanning 1,994 unique block sessions.
2. **Ground Truth Labels (`anomaly_label.csv`)**:
   - Each session block is labeled as either `Normal` or `Anomaly`.
   - Labels were determined via handcrafted domain rules and verified by Hadoop systems experts.
   - Ground truth distribution across the benchmark slice:
     - Total Block Sessions: 1,994
     - Normal Sessions: 1,926 (96.59%)
     - Anomalous Sessions: 68 (3.41%)
   - For repository size efficiency and reproducibility, `data/benchmarks/hdfs/raw/anomaly_label.csv` (64 KB) contains the ground truth labels for the 1,994 benchmark blocks. The complete 575,061-label file (`anomaly_label_full.csv`, 18 MB) is preserved locally and excluded from Git tracking via `.gitignore`.
3. **Templates & Events (`HDFS_2k.log_templates.csv`)**:
   - Log messages are parsed into standardized event templates (e.g., `E1` through `E29`).
   - Event templates strip variable parameters (IP addresses, ports, timestamps, byte sizes, block IDs) to reveal structural execution patterns.

## Directory Structure

```text
data/benchmarks/hdfs/
├── README.md                          # This provenance and metadata document
├── raw/                               # Raw immutable source data
│   ├── HDFS_2k.log                    # Raw unstructured log entries
│   ├── HDFS_2k.log_structured.csv     # Structured log entries with EventId and parameters
│   ├── HDFS_2k.log_templates.csv      # Distinct event template definitions
│   └── anomaly_label.csv              # Ground truth labels by BlockId
└── processed/                         # Processed sequences, splits, and vocabulary
    ├── train_sessions.json            # Deterministic training split (100% Normal sessions)
    ├── val_sessions.json              # Validation split (Stratified Normal + Anomalous)
    ├── test_sessions.json             # Test split (Stratified Normal + Anomalous)
    ├── vocab.json                     # Event ID to token ID mapping with special tokens
    └── preprocessing_metadata.json    # Complete preprocessing statistics and split metrics
```

## Self-Supervised LogBERT Training Policy

In accordance with self-supervised anomaly detection principles:
- **Zero Leakage**: The training split contains **strictly 0 anomalous sessions** (`label == 0`).
- **Disjoint Sessions**: Every `block_id` appears in exactly one split (`train`, `val`, or `test`). No session appears across split boundaries.
