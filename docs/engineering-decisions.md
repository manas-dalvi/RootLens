# RootLens — Core Engineering Decisions (ADR)

This document formalizes the foundational engineering decisions, architectural trade-offs, and boundary definitions for the **RootLens** platform.

---

## Decision 1: Dual-Storage Strategy (PostgreSQL vs. OpenSearch)

### Context & Decision
A common failure mode in observability systems is using a single storage engine for both high-throughput log events and relational application state. RootLens explicitly divides responsibilities across two dedicated databases:
- **PostgreSQL**: Stores relational application metadata, structured incident definitions, analysis runs, saved queries, and user annotations.
- **OpenSearch**: Stores, indexes, and queries high-volume, timestamped log documents and provides date histogram aggregations and full-text search.

### Rationale
- Logs are append-heavy, semi-structured, and require high-throughput text indexing and tokenized searches. Relational databases degrade significantly when handling raw multi-gigabyte log event streams with text search queries.
- Incident tracking, user preferences, and run configurations require ACID compliance, relational integrity, foreign key constraints, and reliable transactional semantics, which document search engines are not optimized to maintain.
- Decoupling search indexing from incident state ensures that an indexing surge never starves or locks incident management metadata.

---

## Decision 2: Ingestion & Normalization Precedes Advanced AI Features

### Context & Decision
Log ingestion, parsing, schema normalization, and structured indexing are implemented and validated **before** building statistical anomaly detection, embedding pipelines, or LLM-based root-cause analysis.

### Rationale
- Advanced AI and machine learning algorithms are only as good as the underlying data quality ("Garbage in, garbage out").
- Disparate systems emit inconsistent log schemas, timestamp formats, and error classifications. Without standardized normalization (e.g., extracting uniform `timestamp`, `service`, `level`, `message`, and `attributes`), automated causality graphs cannot cross-correlate events across services.
- Building and validating a performant search and query layer first guarantees that engineers can manually inspect and verify logs before relying on automated AI summaries.

---

## Decision 3: Evidence-Backed RCA & Mandatory Uncertainty Handling

### Context & Decision
Root Cause Analysis must be strictly evidence-backed. If log evidence, correlation confidence, or timestamp resolution is insufficient to determine causality, the system **must report uncertainty with explicit diagnostic metrics**, rather than presenting a fabricated or speculative conclusion.

### Rationale
- In enterprise SRE operations, an incorrect root-cause diagnosis leads to misplaced engineering efforts, prolonged downtime, and severe operational risk.
- A platform that transparently communicates *"Confidence: Low — insufficient evidence between 14:02 and 14:05 due to missing database logs"* is substantially more valuable and trustworthy than a tool that guesses a false root cause with false confidence.
- Deterministic heuristic thresholds (e.g., minimum co-occurrence, temporal precedence, anomaly confidence) govern the evidence assembly pipeline.

---

## Decision 4: Role of the LLM: Explanatory Synthesis, Not Primary Detector

### Context & Decision
The Large Language Model (Llama-family model) acts solely as an **explanatory and synthesizing layer**, not as the primary root-cause detection engine.

### Rationale
- **Failure Mode of LLMs on Raw Logs**: Passing thousands of raw, unstructured log lines directly to an LLM context window is cost-prohibitive, token-inefficient, non-deterministic, and prone to severe hallucinations.
- **RootLens Approach**:
  1. The **statistical and heuristic analysis engine** analyzes time-series patterns, isolates anomalous spikes, and traces cascading dependency failures deterministically.
  2. The **evidence builder** compiles a compact, structured evidence bundle containing confirmed timestamps, culprit logs, and affected services.
  3. The **LLM** receives *only* the structured evidence bundle and translates it into clear, plain-English incident narratives and actionable remediation suggestions.
- If the analysis engine flags an incident as inconclusive, the prompt constraints forbid the LLM from inventing external causes.

---

## Decision 5: Explicit Scope Boundaries (No Premature Distributed Infrastructure)

### Context & Decision
The following technologies and architectural paradigms are explicitly excluded from the current project scope:
- **No Apache Kafka**: Synchronous REST ingestion and batch file ingestion cover all target project requirements without the immense memory and operational overhead of a Kafka cluster and ZooKeeper/KRaft.
- **No Kubernetes**: System deployment is targeted for single-host Docker Compose and standard developer environments.
- **No Redis / Celery**: Ingestion and analysis workloads will use native FastAPI async endpoints and background task queues, avoiding unnecessary cache synchronization layers.
- **No Distributed Microservices**: The backend is implemented as a modular FastAPI monolith with clean service boundaries.
- **No Automated Remediation**: The platform provides operator intelligence, root-cause diagnostics, and actionable recommendations. It deliberately does not execute autonomous server restarts, rollbacks, or destructive infrastructure actions.

### Rationale
- Maintaining a lean, robust architecture prevents overengineering and ensures high code quality, reliable testing, and rapid development within the B.Tech project timeline.
