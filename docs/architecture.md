# RootLens — System Architecture Specification

## 1. System Overview

**RootLens** is an AI-powered enterprise log intelligence and root cause analysis (RCA) platform designed to ingest, normalize, and index high-volume distributed logs, isolate anomalous failure cascades, construct deterministic evidence graphs, and generate evidence-grounded incident explanations.

RootLens pairs a Python/FastAPI backend with a Next.js/TypeScript frontend, backed by PostgreSQL for metadata and OpenSearch for high-speed log search.

---

## 2. Frozen Technology Stack

| Layer | Component | Technology | Role / Responsibility |
| :--- | :--- | :--- | :--- |
| **Frontend** | User Interface | **Next.js (App Router), TypeScript, Tailwind CSS** | Interactive web dashboard for log exploration, filtering, timeline inspection, and RCA reports |
| **Frontend** | Visualizations | **Recharts** | Interactive time-series log volume charts, error spike histograms, and metric distributions |
| **Backend** | API & Core Logic | **Python, FastAPI** | REST APIs, log ingestion/parsing pipeline, anomaly detection, incident orchestrator, RCA service |
| **Backend** | Auth & Security | **PyJWT (HS256) + Gateway Secret** | Cryptographic session verification, RBAC permissions, and trusted gateway mutual auth |
| **Storage** | Application DB | **PostgreSQL 16+** | Relational storage for application metadata, incident records, run configurations, and audit ledgers |
| **Storage** | Search & Indexing | **OpenSearch 2.x** | Distributed search engine for indexing, full-text searching, log filtering, and time-series aggregations |
| **AI / ML** | Anomaly Scoring | **PyTorch + LogBERT** | 4-layer transformer for Masked Log Key Prediction (MLKP) and Vector Health Metric (VHM) scoring |
| **AI / NLP** | Embeddings | **Sentence Transformers** | Log template embedding generation for semantic search and log pattern clustering |
| **AI / NLP** | Explanation LLM | **Llama-family model** (Local/Self-hosted) | Grounded natural language explanation and synthesis strictly derived from deterministic evidence graphs |

---

## 3. High-Level Architectural Diagram

```mermaid
flowchart TD
    subgraph Client ["Client Layer (Next.js 16 + TypeScript)"]
        UI["Web Dashboard & Incident Workspace"]
        Charts["Time-Series Analytics & Histograms (Recharts)"]
    end

    subgraph Backend ["FastAPI Backend Application (Python 3.12)"]
        API["FastAPI REST Router (/api/v1)"]
        Auth["Auth & RBAC Guard (JWT / Gateway Secret)"]
        Parser["Parser & Normalization Pipeline"]
        Cluster["Log Template Drain Clustering"]
        Correlator["Multi-Service Event Correlator"]
        LogBERT["LogBERT Anomaly Inference Engine"]
        RCA["Evidence-Grounded RCA Synthesizer"]
        Decisions["Operator Hypothesis Decision Ledger"]
    end

    subgraph Storage ["Persistence & Retrieval Layer"]
        PG[("PostgreSQL 16\n(Incidents, Evidence, Audit Decisions)")]
        OS[("OpenSearch 2.x\n(Log Indexing & Search Engine)")]
    end

    UI <-->|HTTP / REST JSON| API
    Charts <--> UI
    API --> Auth
    Auth --> API
    API --> Parser
    Parser -->|Normalized Logs| OS
    Parser -->|Job & Source Metadata| PG
    Cluster --> Correlator
    Correlator -->|Incident Candidates| PG
    LogBERT -->|Anomaly Scores & Attributions| PG
    PG --> RCA
    RCA -->|Grounding Check| Decisions
    Decisions -->|Immutable Decisions| PG
```

---

## 4. End-to-End System Workflow Diagrams

### 4.1 Log Ingestion & Normalization Flow

```mermaid
sequenceDiagram
    autonumber
    actor Client as Client / Shipper
    participant API as Ingestion Endpoint (/api/v1/logs/upload)
    participant Disk as Staging Storage (data/uploads/)
    participant Parser as Parsing Service
    participant Normalizer as Normalization Service
    participant PG as PostgreSQL (log_sources, upload_jobs)
    participant OS as OpenSearch (rootlens-logs-*)

    Client->>API: POST /api/v1/logs/upload (multipart file)
    API->>Disk: Stream file to disk (UUID-named staging)
    API->>PG: INSERT log_source & upload_job (PENDING)
    API-->>Client: HTTP 201 Created (job_id, source_id)

    Client->>API: POST /api/v1/logs/jobs/{job_id}/parse
    API->>Parser: Parse file from disk (JSON / Grok / Syslog)
    Parser->>PG: UPDATE upload_job (status=PARSED, total_lines)
    API-->>Client: HTTP 200 OK (parsed count)

    Client->>API: POST /api/v1/logs/jobs/{job_id}/normalize
    API->>Normalizer: Normalize timestamps to UTC ISO-8601, map log levels
    Normalizer->>PG: UPDATE upload_job (status=COMPLETED)
    Normalizer->>OS: Bulk index documents (if OS available; fallback gracefully)
    API-->>Client: HTTP 200 OK (valid normalized records)
```

---

### 4.2 Multi-Service Event Correlation & Incident Creation Flow

```mermaid
flowchart TD
    Logs["Normalized Log Streams Across Microservices"] --> Cluster["Drain Template Miner & Pattern Clustering"]
    Cluster --> Window["Configurable Temporal Sliding Window"]
    
    Window --> Correlator{"Multi-Service Correlator\n(Taxonomy Evaluation)"}
    
    Correlator -->|Shared Trace ID| T1["TRACE_BASED (100% Confidence)"]
    Correlator -->|Shared Span ID| T2["SPAN_BASED (80% Confidence)"]
    Correlator -->|HTTP Request ID| T3["REQUEST_BASED (70% Confidence)"]
    Correlator -->|Single Service Intra-Link| T4["SERVICE_LOCAL (60% Confidence)"]
    Correlator -->|Time Proximity Cascade| T5["TEMPORAL_CASCADE (35% Confidence)"]
    
    T1 --> Candidate["Incident Candidate Assembly\n(Timeline, Blast Radius, Primary Culprit)"]
    T2 --> Candidate
    T3 --> Candidate
    T4 --> Candidate
    T5 --> Candidate

    Candidate --> Guard{"Disjoint Trace Isolation Guard"}
    Guard -->|Conflicting Trace IDs| Isolate["Isolate into Separate Incidents"]
    Guard -->|Consistent Traces| Save["Persist Incident to PostgreSQL\n(status: DETECTED, is_root_cause_confirmed: false)"]
```

---

### 4.3 Evidence-Grounded RCA Flow

```mermaid
flowchart TD
    Incident["Active Incident Entity in PostgreSQL"] --> Fetch["Retrieve Incident Timeline Events & Evidence Links"]
    Fetch --> LogBERT["LogBERT Inference (MLKP Anomaly Score + VHM Embedding Distance)"]
    
    LogBERT --> Explain{"Explainability Classification"}
    Explain -->|MLKP Dominant| M1["TOKEN_MISMATCH"]
    Explain -->|VHM Dominant| M2["EMBEDDING_DISTANCE"]
    Explain -->|Both Elevated| M3["HYBRID"]
    Explain -->|Within Normal Range| M4["NORMAL"]
    
    M1 --> Synthesizer["RCA Synthesis Engine (Candidate Hypotheses & Factual Claims)"]
    M2 --> Synthesizer
    M3 --> Synthesizer
    M4 --> Synthesizer

    Synthesizer --> CitationVal{"Citation Validation Guardrail"}
    CitationVal -->|Cites Indexed Evidence Logs| Valid["SUPPORTED_FACT\n(Valid Claim with Log Provenance)"]
    CitationVal -->|Cites Non-Existent or Fabricated Log| Reject["REJECTED / QUARANTINED\n(Excluded from Emitted RCA Findings)"]
    CitationVal -->|No Citing Telemetry Available| Gap["TELEMETRY_GAP\n(Explicit Uncertainty Boundary)"]

    Valid --> Emitted["Final Evidence-Backed RCA Finding\n(Candidate Root Cause + Explanations)"]
    Gap --> Emitted
```

---

### 4.4 Human Operator Decision & Audit Ledger Workflow

```mermaid
sequenceDiagram
    autonumber
    actor Op as SRE / On-Call Operator
    participant UI as Incident Workspace (/incidents/[id])
    participant Auth as Auth Middleware (Bearer Token)
    participant DecisionSvc as Hypothesis Decision Service
    participant PG as PostgreSQL (rca_hypothesis_decisions, incidents)

    Op->>UI: Inspect Candidate Root Cause & Supporting Citations
    Op->>UI: Select Action: CONFIRM or REJECT (with mandatory notes)
    UI->>Auth: POST /api/v1/incidents/{id}/hypotheses/{hyp_id}/confirm
    Auth->>Auth: Validate JWT / RBAC (Must be OPERATOR or ADMIN)
    Auth->>DecisionSvc: Dispatch with OperatorIdentity (operator_id, email)
    
    DecisionSvc->>PG: Lock Incident Row (with_for_update)
    DecisionSvc->>DecisionSvc: Strict Check: Does hypothesis have verified citations?
    alt No Supporting Citations
        DecisionSvc-->>UI: HTTP 400 Bad Request (Cannot confirm ungrounded hypothesis)
    else Evidence Citations Verified
        DecisionSvc->>PG: Snapshot Citations (ISO-8601 serialized JSONB)
        DecisionSvc->>PG: INSERT INTO rca_hypothesis_decisions (append-only ledger)
        DecisionSvc->>PG: UPDATE incidents SET is_root_cause_confirmed = true, confirmed_root_cause_id = hyp_id
        DecisionSvc->>PG: COMMIT
        DecisionSvc-->>UI: HTTP 200 OK (Decision Record)
        UI-->>Op: Display "Operator Confirmed" badge and update audit ledger
    end
```

---

## 5. Scope Constraints & Non-Goals

To maintain a disciplined, high-quality enterprise capstone architecture:
- ❌ **No Automated Remediation**: The platform informs, explains, and guides human operators; it does not execute automated server mutations or code rollbacks.
- ❌ **No Multi-Tenant SaaS Sharding**: The platform is an internal enterprise single-tenant platform. Multi-tenancy is handled via independent deployment stacks.
- ❌ **No Heavy Message Brokers**: Synchronous streaming and batch upload endpoints fulfill all throughput requirements without the operational burden of Apache Kafka or RabbitMQ.
