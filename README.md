# RootLens

> **AI-Powered Enterprise Log Intelligence & Root Cause Analysis Platform**  
> *Production-Ready Log Observability, Anomaly Detection, Multi-Service Correlation, and Evidence-Grounded RCA*

---

## 1. Project Overview & Problem Statement

Modern cloud-native and distributed enterprise software systems generate millions of log events across interdependent microservices every minute. During critical production outages:
- **Alert Fatigue & Log Noise**: Engineers are flooded with cascading downstream errors (e.g., HTTP 504s, connection timeouts) that conceal the real upstream trigger (e.g., database connection pool exhaustion or a failed secret rotation).
- **High Mean Time to Resolution (MTTR)**: Manually querying, filtering, and correlating timestamps across disparate services takes hours of manual effort.
- **Unreliable "Black-Box" AI**: Naive implementations that dump raw logs into generative LLMs suffer from severe hallucinations, missing context windows, and speculative diagnoses lacking concrete evidence.

**RootLens solves this** through an evidence-first, human-in-the-loop diagnostic pipeline:
1. **Streaming Ingestion & Normalization**: Tokenizes, validates, and normalizes unstructured and JSON logs into canonical UTC ISO-8601 schemas.
2. **Deep Anomaly Detection (LogBERT)**: Utilizes a self-supervised PyTorch transformer trained on log sequences to detect anomalous log keys (MLKP) and semantic embedding shifts (VHM) with low false-positive rates.
3. **Deterministic Multi-Service Correlation**: Correlates events across microservices using a 5-tier taxonomy (`TRACE_BASED` 100%, `SPAN_BASED` 80%, `REQUEST_BASED` 70%, `SERVICE_LOCAL` 60%, `TEMPORAL_CASCADE` 35%), isolating disjoint traces and calculating blast radius.
4. **Evidence-Grounded RCA Synthesis**: Formulates candidate root cause hypotheses strictly backed by verified, indexed telemetry citations. Hypotheses without evidence are quarantined as ungrounded speculations.
5. **Human-in-the-Loop Operator Decisioning**: Strictly maintains a **0.0% automated confirmation invariant**. AI proposes hypotheses; only authenticated operators can confirm or reject root causes, recording an immutable audit ledger.
6. **Actionable Recommendations & Exports**: Delivers structured diagnostic verification steps and mitigation advisories with CSV/JSON streaming export capabilities.

---

## 2. Features & Capabilities

- **High-Throughput Ingestion**: Direct multipart streaming log ingestion with auto-registration of sources and background parser jobs.
- **LogBERT Anomaly Scoring**: 4-layer transformer model scoring log key prediction and vector health metrics with 400+ sequences/second throughput.
- **Explainability Badges**: Automatic classification of anomalies into `TOKEN_MISMATCH`, `EMBEDDING_DISTANCE`, `HYBRID`, or `NORMAL`.
- **Incident Lifecycle Management**: Relational tracking of incidents through `DETECTED`, `INVESTIGATING`, `MITIGATED`, `RESOLVED`, and `CLOSED` states.
- **5-Tab Incident Workspace**:
  - **Overview & RCA**: Primary culprit hypothesis, blast radius services, confidence scores, and operator review controls.
  - **Timeline & Evidence**: Chronological failure event progression with telemetry badges, missing telemetry gap indicators, and deep-links to raw logs.
  - **AI Investigation**: Interactive chat and hypothesis exploration grounded in incident telemetry.
  - **Recommendations**: Phased mitigations, verification procedures, and diagnostic caveats.
  - **Post-Mortem Reports**: Formatted incident summaries and exportable audit documents.
- **Cryptographic Security & RBAC**: JWT (HMAC-SHA256) session validation, trusted gateway mutual secret verification (`X-Gateway-Secret`), production developer token blocking, and role-based permissions (`VIEWER`, `OPERATOR`, `ADMIN`, `LEAD_SRE`).
- **Resilient Dual Storage**: PostgreSQL 16+ for relational entities and transactional audit ledgers, paired with OpenSearch for distributed log search, complete with graceful HTTP 503 offline degradation.
- **Formula-Safe Data Export**: Paginated streaming export to CSV and JSON with CSV formula injection defense (`=`, `+`, `-`, `@` sanitization).

---

## 3. High-Level Architecture

RootLens decouples fast search indexing from relational incident management and AI inference:

```mermaid
flowchart TD
    subgraph Client ["Client Layer (Next.js 16 + React)"]
        UI["Web Dashboard & Incident Workspace"]
        Charts["Time-Series Analytics & Histograms (Recharts)"]
    end

    subgraph Backend ["Backend Application (FastAPI / Python 3.12)"]
        API["FastAPI REST Router (/api/v1)"]
        Auth["Auth & RBAC Guard (JWT / Gateway Secret)"]
        Parser["Parser & Normalization Pipeline (ParserFactory)"]
        Cluster["Log Template Clustering & Sequence Generation"]
        Correlator["5-Tier Multi-Service Correlator"]
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
    Decisions -->|Immutable Audit Ledger| PG
```

---

## 4. Technology Stack

| Layer | Component | Version / Technology | Purpose |
| :--- | :--- | :--- | :--- |
| **Frontend** | Framework | Next.js 16.4.0 (App Router) | High-performance React server and client components |
| **Frontend** | Language | TypeScript 5.x | Strict type contracts and interface guarantees |
| **Frontend** | Styling | Tailwind CSS 3.4 | Modern, responsive dark-mode operations UI |
| **Frontend** | Charts | Recharts | Interactive time-series log histograms and distributions |
| **Backend** | Framework | FastAPI / Starlette | Asynchronous, OpenAPI-documented REST API |
| **Backend** | Language | Python 3.12 | Core backend application language |
| **Backend** | ORM / DB Driver | SQLAlchemy 2.0 + asyncpg | Asynchronous relational data access and migrations |
| **Backend** | Migrations | Alembic | Version-controlled PostgreSQL schema evolution |
| **Storage** | Application DB | PostgreSQL 16+ | Incidents, timeline events, evidence, and audit logs |
| **Storage** | Search Engine | OpenSearch 2.x | Distributed log indexing, full-text search, aggregations |
| **Machine Learning** | Anomaly Detection | PyTorch + Custom LogBERT | Self-supervised transformer for log key and sequence scoring |
| **AI / NLP** | Embeddings | Sentence Transformers | Log template semantic similarity and clustering |

---

## 5. Prerequisites

Before running RootLens, ensure the following software is installed on the host system:
- **Python**: Version `3.12+`
- **Node.js**: Version `20.x` or `22.x` (LTS recommended)
- **PostgreSQL**: Version `16+` (running on port `5432`)
- **Docker** *(Recommended for OpenSearch)*: Docker Engine or Docker Desktop
- **Git**: Version `2.x+`

---

## 6. Installation & Configuration

### A. Clone the Repository
```bash
git clone https://github.com/your-org/RootLens.git
cd RootLens
```

### B. Environment Configuration
Create a local `.env` file from the provided template:
```bash
cp .env.example .env
```
Configure `.env` with your local database credentials (never commit real credentials):
```ini
# Backend Configuration
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
ENVIRONMENT=development
DEBUG=True
SECRET_KEY=change_this_to_a_secure_random_secret_in_production
ALLOW_DEV_TOKENS=True

# PostgreSQL Database Configuration
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=rootlens
POSTGRES_USER=rootlens_user
POSTGRES_PASSWORD=your_secure_password
DATABASE_URL=postgresql://rootlens_user:your_secure_password@localhost:5432/rootlens

# OpenSearch Configuration
OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9200
OPENSEARCH_USE_SSL=false
OPENSEARCH_VERIFY_CERTS=false
OPENSEARCH_INDEX_PREFIX=rootlens-logs

# Frontend Configuration
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
```

### C. Backend Setup
1. Create and activate a Python virtual environment:
   ```bash
   # Windows (PowerShell)
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1

   # Linux / macOS
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Install Python dependencies:
   ```bash
   pip install -r backend/requirements.txt
   ```

### D. PostgreSQL Setup & Migrations
1. Ensure the PostgreSQL service is running and create the `rootlens` database:
   ```sql
   CREATE USER rootlens_user WITH PASSWORD 'your_secure_password';
   CREATE DATABASE rootlens OWNER rootlens_user;
   GRANT ALL PRIVILEGES ON DATABASE rootlens TO rootlens_user;
   ```
2. Run database migrations to apply the relational schema:
   ```bash
   alembic -c backend/alembic.ini upgrade head
   ```

### E. OpenSearch Setup (Docker)
Launch a local single-node OpenSearch 2.x instance via Docker:
```bash
docker run -d \
  -p 9200:9200 -p 9600:9600 \
  -e "discovery.type=single-node" \
  -e "plugins.security.disabled=true" \
  --name rootlens-opensearch \
  opensearchproject/opensearch:2
```
*Note: If OpenSearch is not running, RootLens gracefully degrades to PostgreSQL relational search for core workflows and reports an informative HTTP 503 on raw log search.*

### F. Frontend Setup
1. Install Node.js dependencies:
   ```bash
   cd frontend
   npm install
   cd ..
   ```

---

## 7. Supported Log Formats & Ingestion Parsers

RootLens uses an extensible `ParserFactory` pattern (`backend/app/parsers/factory.py`) to parse, normalize, and validate inbound telemetry:

| Format Name | Parser Class | Typical Source | Features & Extracted Metadata |
| :--- | :--- | :--- | :--- |
| **`hdfs`** | `HDFSLogParser` | Hadoop / Distributed File Systems | Extracts block IDs (`blk_-?[0-9]+`), maps to canonical templates `E1`–`E29`, generates session sequences for LogBERT inference |
| **`json`** | `JSONLogParser` | Cloud-Native Microservices, Kubernetes | Dynamic schema detection, extracts `timestamp`, `service`, `level`, `message`, `trace_id`, `span_id`, and structured attributes |
| **`syslog`** | `SyslogParser` | Linux Daemons, Appliances, Routers | RFC 3164 and RFC 5424 compliant; extracts PRI, facility, severity, hostname, and message payload |
| **`application_log`** | `ApplicationLogParser` | Logback, Log4j, Python logging | Robust unstructured regex parser with ISO/custom timestamp normalization, level extraction, and thread/class identification |
| **`csv`** | `CSVLogParser` | Batch exports, DB query logs | Header auto-detection and column-to-canonical schema mapping |

---

## 8. LogBERT Anomaly Detection Engine

RootLens includes a pre-trained PyTorch LogBERT model checkpoint calibrated on the full HDFS benchmark dataset:
- **Checkpoint Location**: `data/benchmarks/hdfs_full/model/logbert_best.pt` (~9.5 MB, tracked in repository)
- **Vocabulary File**: `data/benchmarks/hdfs_full/processed/vocab.json` (22 active tokens, 5 special tokens + 17 normal templates)
- **Hypersphere Center**: `data/benchmarks/hdfs_full/model/hypersphere_center.pt`
- **Model Metadata**: `data/benchmarks/hdfs_full/model/config.json`
- **Decision Threshold**: `0.0004` (strictly calibrated on held-out validation loss minimum)

No additional model downloads or training steps are required to run inference out of the box.

### Full Benchmark Dataset Acquisition (Optional for Retraining)
The complete HDFS benchmark dataset (11,175,629 lines, 575,061 sessions) is hosted on Zenodo:
- **Zenodo DOI**: [10.5281/zenodo.8196385](https://doi.org/10.5281/zenodo.8196385)
- **Source Archive**: `HDFS_v1.zip`
- **Verified MD5**: `76a24b4d9a6164d543fb275f89773260`
- **Dataset License**: CC-BY 4.0
- **Usage**: To retrain or run full 11-million-line benchmark evaluations, download `HDFS_v1.zip` from Zenodo and extract into `data/benchmarks/hdfs_full/raw/`. Sample sessions (`train_sessions_sampled.json`, `test_sessions_sampled.json`, `HDFS_2k.log`) are pre-included in the repository for immediate testing.

---

## 9. Multi-Service Correlation & Evidence-Grounded RCA

### 5-Tier Correlation Taxonomy
RootLens classifies cross-service causal relationships using a deterministic confidence taxonomy:
1. **`TRACE_BASED` (100% confidence)**: Direct distributed trace link sharing the identical `trace_id`.
2. **`SPAN_BASED` (80% confidence)**: Parent-child relationship linked via `span_id` and `parent_span_id`.
3. **`REQUEST_BASED` (70% confidence)**: Shared correlation identifier across HTTP/RPC boundaries (`request_id` / `correlation_id`).
4. **`SERVICE_LOCAL` (60% confidence)**: Same-service internal failures within the incident temporal window.
5. **`TEMPORAL_CASCADE` (35% confidence)**: Downstream service failure occurring within the latency propagation window following an upstream error.

### Evidence Grounding & Operator Decision Safety
- **Strict Evidence Traceability**: Every AI-synthesized root-cause claim must cite verified, indexed telemetry records. Hypotheses without backing evidence are flagged or quarantined.
- **0.0% Automated Confirmation Invariant**: AI algorithms generate and rank diagnostic hypotheses, but **cannot confirm root causes**. Only an authenticated operator or SRE with appropriate credentials (`OPERATOR`, `LEAD_SRE`, `ADMIN`) can confirm an incident root cause (`is_root_cause_confirmed = True`), appending an immutable entry to the decision audit ledger.

---

## 10. Running the Application

### 1. Start the Backend API Server
With the virtual environment active:
```bash
# Windows / Linux
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```
The FastAPI backend will start at `http://127.0.0.1:8000`.
- Swagger API Documentation: `http://127.0.0.1:8000/docs`
- Health Endpoint: `http://127.0.0.1:8000/api/v1/health`

### 2. Start the Frontend Development Server
In a separate terminal:
```bash
cd frontend
npm run dev
```
The web dashboard will be available at `http://localhost:3000`.

---

## 11. Running Tests & Verification

### Backend Unit & Integration Tests (pytest)
```bash
# Windows (PowerShell)
$env:PYTHONPATH="backend"
pytest backend/tests -v

# Linux / macOS
PYTHONPATH=backend pytest backend/tests -v
```
*Current test suite: **565 passed** across all 35 test suites.*

### Frontend Node Contract Tests
```bash
node --test tests/*.mjs
```

### Static Type Checking (Pyright)
```bash
npx pyright --pythonpath .venv/Scripts/python.exe backend/
```

### Frontend Linting & Production Build
```bash
cd frontend
npm run lint
npm run build
```

---

## 12. Reproducible Demonstration Walkthrough

To execute a complete end-to-end incident triage demonstration:

1. **Verify Services**: Confirm PostgreSQL is running (`SELECT 1`) and the backend API is live at `http://127.0.0.1:8000/api/v1/health`.
2. **Access Web UI**: Navigate to `http://localhost:3000` in a browser.
3. **Select Sample Scenario**: Locate the multi-service failure dataset at `data/samples/scenario_db_cascade.json` (Database connection pool exhaustion cascading to order and payment services).
4. **Ingest Logs**:
   - Navigate to `/logs/upload` on the dashboard.
   - Upload `scenario_db_cascade.json` specifying service name `postgres-db` and environment `production`.
   - The system stages, parses, and normalizes the log records.
5. **View Incident**:
   - Navigate to `/incidents`. Locate the detected incident: *"PostgreSQL connection exhaustion cascading to order and payment services"*.
   - Click to enter the Incident Workspace (`/incidents/[id]`).
6. **Inspect Timeline & Evidence**:
   - Switch to the **Timeline & Evidence** tab.
   - Observe the initial trigger event at `08:01:30Z` (`postgres-db`), followed by order-service pool timeout at `08:01:35Z`, and payment circuit-breaker trip at `08:01:50Z`.
   - Note the `TRACE-LINKED (100%)` relationship badge and missing telemetry gap indicators.
7. **Inspect RCA Hypotheses**:
   - Switch to the **Overview & RCA** tab.
   - Review the primary hypothesis citing the exact log line `active=100 max=100`.
   - Verify that all claims display grounding status (`SUPPORTED_FACT` with clickable citations).
8. **Review Recommendations**:
   - Switch to the **Recommendations** tab.
   - Observe mitigation advisories: `Increase PostgreSQL max_connections` and `Review HikariPool connection timeout settings`.
9. **Operator Decision**:
   - Under the RCA hypothesis review panel, enter review justification: *"Confirmed connection pool saturation via database metrics."*
   - Click **Confirm Root Cause**.
   - Observe that `is_root_cause_confirmed` updates to `True` and the decision is appended to the immutable audit ledger.
10. **Export Incident**:
    - Click the **Export** button in the header.
    - Choose **Export as JSON** or **Export as CSV**. Verify the sanitized, formula-safe file downloads successfully.

---

## 13. Evaluation & Verification Evidence

RootLens maintains strict transparency across all evaluation metrics:

- **LogBERT Benchmark (HDFS Held-Out Test Set)**:
  - 64,242 evaluated sessions (8,419 anomalies, 55,823 normal).
  - **Precision**: `97.45%` | **Recall**: `52.24%` | **F1 Score**: `68.02%` | **FPR**: `0.21%` | **Specificity**: `99.79%`.
  - Full report: [`docs/benchmark-evaluation-report.md`](docs/benchmark-evaluation-report.md).
- **Multi-Service RCA Ground-Truth Benchmark**:
  - 12 synthetic multi-service failure scenarios.
  - **Top-1 Accuracy (Classifiable)**: `100.0%` (11/11).
  - **Recall@K Evidence Retrieval**: `100.0%` (11/11).
  - **Citation Validity**: `100.0%` (84/84).
  - **False Confirmation Rate**: `0.0%` (0/12) — strict safety invariant.
  - Full report: [`docs/rca_evaluation_report.md`](docs/rca_evaluation_report.md).
- **End-to-End Integration & Security Verification**:
  - 12 of 12 lifecycle stages verified across 20 automated tests.
  - Full report: [`docs/phase11_integration_verification_report.md`](docs/phase11_integration_verification_report.md).

---

## 14. Known Limitations

- **OpenSearch Deployment**: The default setup runs single-node OpenSearch for development. Production deployments require a multi-node cluster with managed indexing policies.
- **Offline Fallback**: When OpenSearch is offline, RootLens gracefully falls back to PostgreSQL relational search for core workflows; full-text search across arbitrary raw logs requires OpenSearch.
- **LogBERT Domain Specialization**: The packaged LogBERT model checkpoint is pre-calibrated on distributed filesystem log structures (HDFS template vocabulary). While effective on sequence patterns, new log domains benefit from domain-specific vocabulary tuning.
- **Hardware Acceleration**: Sequential CPU inference achieves 400+ sequences/second; massive multi-gigabyte backfills or high-throughput real-time ingestion pipelines benefit from GPU acceleration (`torch.cuda`).
- **Human-in-the-Loop Constraint**: RootLens strictly does not support automated incident closure or autonomous root-cause confirmation by design.

---

## 15. Troubleshooting Guide

| Issue / Symptom | Possible Cause | Resolution |
| :--- | :--- | :--- |
| **Database Connection Refused** (`ConnectionRefusedError: 5432`) | PostgreSQL is not running or incorrect credentials in `.env` | Verify PostgreSQL service status (`Get-Service postgresql*` or `sudo systemctl status postgresql`) and verify `DATABASE_URL` in `.env`. |
| **OpenSearch 503 Unavailable** (`OpenSearch cluster is currently unavailable`) | OpenSearch is not running on port 9200 | Expected behavior in offline development mode. The backend gracefully catches connection errors and continues operating PostgreSQL workflows. To enable full-text log search, launch OpenSearch via Docker (`docker start rootlens-opensearch`). |
| **Alembic Target Database Out of Date** | Pending schema migrations | Run `alembic -c backend/alembic.ini upgrade head` to ensure all migration revisions are applied. |
| **CORS Origin Blocked** | Frontend port mismatch | Ensure `BACKEND_CORS_ORIGINS` in `.env` includes `http://localhost:3000` or your active frontend URL. |
| **JWT Unauthorized on Hypothesis Mutation** | Missing or invalid auth header | In development, pass `Authorization: Bearer dev-lead-sre-token` or use the authenticated UI review controls. |

---

## License

Distributed for research and operational observability evaluation.

