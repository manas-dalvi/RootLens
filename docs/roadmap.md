# RootLens — Project Implementation Roadmap

This roadmap documents the 20 implementation phases for **RootLens: AI-Powered Enterprise Log Intelligence & Root Cause Analysis Platform**. 

Each phase builds sequentially upon verified foundations, adhering to the core principle that robust log ingestion, parsing, and indexing must precede anomaly detection, deterministic RCA, and LLM explanation.

---

## Roadmap Overview

```
Phase 01: Architecture Specification [COMPLETED]
Phase 02: Repository Initialization   [IN PROGRESS - Current Task]
Phase 03: FastAPI Backend Scaffolding
Phase 04: PostgreSQL Metadata Models
Phase 05: OpenSearch Cluster & Index Mapping
Phase 06: Sample Log Data Curation
Phase 07: Log Ingestion & Normalization Engine
Phase 08: OpenSearch Search & Query APIs
Phase 09: Next.js Frontend Scaffolding
Phase 10: Log Explorer & Filtering Interface
Phase 11: Time-Series Visualizations (Recharts)
Phase 12: Statistical Anomaly Detection
Phase 13: Semantic Embeddings (Sentence Transformers)
Phase 14: Incident Aggregation & Timeline Reconstruction
Phase 15: Deterministic RCA & Evidence Graph Builder
Phase 16: Grounded LLM Explanation Service (Llama-family)
Phase 17: Incident & RCA Investigation Dashboard
Phase 18: End-to-End Integration & Verification
Phase 19: Comprehensive Test Suite & Robustness Benchmarks
Phase 20: Documentation & Demonstration Readiness
```

---

## Detailed Phase Breakdown

### Phase 01: Project Scope, Requirements Definition & Architecture Specification
- **Status**: Completed
- **Objectives**: 
  - Define platform objectives, user persona (SRE/DevOps/Engineers), and B.Tech project boundaries.
  - Freeze technology stack (FastAPI, Next.js, PostgreSQL, OpenSearch, Recharts, Sentence Transformers, Llama-family model).
  - Explicitly exclude non-goals (no Kafka, Kubernetes, Redis, Celery, automated remediation, or premature microservices).

### Phase 02: Repository Setup, Structure & Environment Initialization
- **Status**: In Progress (Current Phase)
- **Objectives**:
  - Establish root repository directory structure (`backend/`, `frontend/`, `data/samples/`, `docs/`, `tests/`).
  - Configure comprehensive `.gitignore` rules for Python, Node.js, secrets, IDEs, and build artifacts.
  - Provide `.env.example` with clear placeholders and exclude real secrets.
  - Document system architecture, engineering decisions, and this 20-phase roadmap in `docs/`.
  - Maintain clean Git history without automated commits.

### Phase 03: Core Backend Foundation & FastAPI Application Scaffolding
- **Status**: Planned
- **Objectives**:
  - Scaffold `backend/` with standard FastAPI project structure (routers, services, core configuration, schemas).
  - Configure Pydantic settings loading from `.env`.
  - Implement health check and readiness endpoints (`/api/v1/health`, `/api/v1/ready`).
  - Configure CORS middleware for Next.js frontend access.

### Phase 04: Relational Database Schema & PostgreSQL Metadata Modeling
- **Status**: Planned
- **Objectives**:
  - Set up PostgreSQL database connection management with SQLAlchemy / asyncpg.
  - Define structured relational tables for incidents, analysis runs, system metadata, and query bookmarks.
  - Implement basic repository layers for metadata CRUD operations.

### Phase 05: OpenSearch Cluster Setup & Log Index Template Configuration
- **Status**: Planned
- **Objectives**:
  - Establish OpenSearch connection client with configurable authentication and host parameters.
  - Define index mappings and settings for normalized log documents (timestamp date format, keyword fields for service/level/host, analyzed text fields for message).
  - Create index templates and aliases for efficient time-partitioned querying.

### Phase 06: Synthetic & Sample Enterprise Log Dataset Curation
- **Status**: Planned
- **Objectives**:
  - Populate `data/samples/` with realistic enterprise log datasets representing multi-service microservice/monolith environments.
  - Include representative normal traffic logs and specific incident scenarios (cascading failures, database connection pool exhaustion, memory leak crash loops, authentication timeouts).
  - Validate baseline log formatting across JSON, Syslog, and standard application log styles.

### Phase 07: Log Ingestion, Multi-Format Parsing & Normalization Engine
- **Status**: Planned
- **Objectives**:
  - Build ingestion pipeline capable of parsing raw log lines (JSON, Grok/regex-based unstructured text).
  - Implement normalization layer that extracts common schema: `timestamp`, `service`, `level`, `message`, `host`, `trace_id`, and structured `attributes`.
  - Implement schema validation and quarantine mechanisms for malformed log entries.

### Phase 08: OpenSearch Log Indexing, Bulk Pipeline & Search API Service
- **Status**: Planned
- **Objectives**:
  - Implement bulk indexing helper for ingesting batches of normalized logs into OpenSearch.
  - Create backend search service exposing multi-parameter filters (time range, service, log level, full-text query).
  - Provide date histogram aggregations for log counts over time.

### Phase 09: Frontend Core Setup (Next.js, TypeScript & Tailwind CSS)
- **Status**: Planned
- **Objectives**:
  - Scaffold `frontend/` using Next.js App Router, TypeScript, and Tailwind CSS.
  - Implement clean layout shell: navigation bar, sidebar, breadcrumbs, theme tokens, and API client wrapper.
  - Verify seamless communication between frontend and FastAPI backend health endpoints.

### Phase 10: Log Explorer, Query Syntax & Multi-Attribute Filter UI
- **Status**: Planned
- **Objectives**:
  - Build interactive log explorer table with pagination, expandable JSON rows, and syntax highlighting.
  - Implement interactive filter bar for log level (INFO, WARN, ERROR, FATAL), service name, and time range.
  - Integrate live search query bar connecting to backend OpenSearch search API.

### Phase 11: Time-Series Aggregations & Analytics Visualizations (Recharts)
- **Status**: Planned
- **Objectives**:
  - Implement interactive time-series log volume charts using Recharts.
  - Add stacked bar charts for log volume by severity level and service breakdown.
  - Support brush-and-zoom time window selection that dynamically filters the log explorer.

### Phase 12: Statistical Anomaly Detection & Error Frequency Pattern Analysis
- **Status**: Planned
- **Objectives**:
  - Implement statistical algorithms (e.g., z-score, rolling moving average, rate-of-change thresholds) on log frequency and error spikes.
  - Detect unprecedented error rate surges across individual services.
  - Flag anomaly windows with statistical confidence scores.

### Phase 13: Log Template Extraction & Semantic Embeddings (Sentence Transformers)
- **Status**: Planned
- **Objectives**:
  - Implement template mining (masking variable tokens like UUIDs, IPs, and timestamps) to isolate log message patterns.
  - Integrate local Sentence Transformers model (e.g., `all-MiniLM-L6-v2`) to generate embeddings for unique log templates.
  - Enable vector-based semantic clustering and novel error pattern discovery in OpenSearch.

### Phase 14: Incident Aggregation, Cross-Service Correlation & Timeline Reconstruction
- **Status**: Planned
- **Objectives**:
  - Aggregate anomalous spikes and correlated error events into distinct incident entities.
  - Reconstruct cross-service chronological timelines identifying the sequence of failure propagation.
  - Persist detected incident records and timeline metadata into PostgreSQL.

### Phase 15: Deterministic Root Cause Analysis (RCA) & Evidence Graph Construction
- **Status**: Planned
- **Objectives**:
  - Build deterministic causality analyzer: identify initial error triggers, upstream dependencies, and downstream cascades.
  - Assemble verifiable evidence bundles: chronological log records, culprit line snippets, error frequencies, and impacted dependencies.
  - Implement strict uncertainty threshold: if evidence is insufficient or conflicting, output explicit uncertainty rather than speculative causality.

### Phase 16: LLM-Assisted Evidence-Grounded Explanation Engine (Llama-family)
- **Status**: Planned
- **Objectives**:
  - Integrate local/self-hosted Llama-family model (e.g., Llama-3-8B-Instruct) via standard inference API.
  - Design constrained prompt templates that supply only the deterministic evidence bundle assembled in Phase 15.
  - Instruct model to synthesize readable root-cause summaries and remediation recommendations strictly grounded in the provided logs.
  - Enforce explicit disclaimers and refusal to hallucinate if evidence is flagged as uncertain.

### Phase 17: Incident & RCA Investigation Dashboard (Frontend UI)
- **Status**: Planned
- **Objectives**:
  - Build incident list and detail views in Next.js.
  - Display interactive chronological incident timeline with highlighted root-cause trigger events.
  - Render LLM-generated explanation cards alongside side-by-side inspectable log evidence snippets.

### Phase 18: End-to-End System Integration & Workflow Verification
- **Status**: Planned
- **Objectives**:
  - Connect full workflow: ingestion -> indexing -> anomaly detection -> incident creation -> RCA evidence generation -> LLM explanation -> frontend display.
  - Test with multi-service outage scenarios from sample datasets.
  - Verify stability, latency, and graceful error handling across all layers.

### Phase 19: Comprehensive Testing (Unit, Integration, RCA Robustness & Uncertainty)
- **Status**: Planned
- **Objectives**:
  - Write comprehensive unit tests for parsers, normalizers, search query builders, and anomaly algorithms.
  - Write API integration tests verifying FastAPI endpoints with PostgreSQL and OpenSearch mocks/test instances.
  - Benchmark RCA accuracy: verify deterministic causality on known incidents and verify that uncertain scenarios yield explicit uncertainty warnings.

### Phase 20: Performance Benchmarking, Final Documentation & Demonstration Readiness
- **Status**: Planned
- **Objectives**:
  - Conduct throughput benchmarking on log ingestion and query latency.
  - Finalize end-to-end user manual, API documentation (OpenAPI / Swagger), and evaluation report for the B.Tech capstone.
  - Prepare repeatable demo scripts and sample incident walkthroughs.
