# RootLens

> **AI-Powered Enterprise Log Intelligence & Root Cause Analysis Platform**  
> *Enterprise Log Observability, Anomaly Detection, Multi-Service Correlation, and Evidence-Grounded Root Cause Analysis*

---

## 1. Project Overview

Modern distributed applications generate large volumes of logs across interconnected microservices. During production failures, engineers must investigate thousands of events to identify the original trigger behind cascading errors.

Common challenges include:

- **Alert Fatigue:** Large volumes of downstream errors hide the original failure.
- **High Mean Time to Resolution:** Manually correlating timestamps, services, and request identifiers takes time.
- **Fragmented Telemetry:** Logs are distributed across different services and storage systems.
- **Unreliable AI Diagnoses:** Generative AI can produce speculative explanations without supporting evidence.

RootLens addresses these challenges through an evidence-first, human-in-the-loop diagnostic pipeline.

### Core Capabilities

1. **Log Ingestion and Normalization:** Ingests structured and unstructured logs, validates records, and converts them into a canonical representation.
2. **LogBERT Anomaly Detection:** Uses a PyTorch-based transformer model for HDFS log-sequence anomaly detection.
3. **Multi-Service Correlation:** Correlates events using trace, span, request, service-local, and temporal relationships.
4. **Evidence-Grounded RCA:** Generates root-cause hypotheses supported by indexed telemetry evidence.
5. **Human-in-the-Loop Decision Making:** Operators retain authority over root-cause confirmation.
6. **Incident Management:** Provides investigation workflows, recommendations, timelines, and report exports.

**Core design principle:** AI-generated explanations must be supported by relevant log evidence. A temporal correlation alone does not establish confirmed causality.

---

## 2. Features and Capabilities

- Log ingestion through multipart uploads.
- Parser-based log format detection and normalization.
- OpenSearch-based log indexing and search.
- PostgreSQL-backed incident and evidence management.
- LogBERT sequence anomaly scoring for supported HDFS log sequences.
- Anomaly classification and explanatory evidence.
- Multi-service incident candidate discovery and correlation.
- Incident lifecycle management.
- Timeline and evidence exploration.
- AI-assisted investigation.
- Diagnostic recommendations and verification steps.
- CSV and JSON report exports.
- JWT-based authentication and role-based access controls.
- Dashboard telemetry, service statistics, and time-series visualizations.

### Incident Workspace

The incident workspace provides:

- **Overview & RCA:** Root-cause hypotheses, confidence information, and operator review.
- **Timeline & Evidence:** Chronological events and links to supporting logs.
- **AI Investigation:** Investigation assistance grounded in incident telemetry.
- **Recommendations:** Diagnostic and mitigation suggestions.
- **Post-Mortem Reports:** Structured incident summaries and exports.

---

## 3. High-Level Architecture

RootLens separates log indexing, relational incident management, anomaly inference, and the frontend interface.

```mermaid
flowchart TD
    A[Log Sources] --> B[Log Ingestion]
    B --> C[Parsing and Normalization]
    C --> D[Storage and Indexing]

    D --> E[OpenSearch]
    D --> F[PostgreSQL]

    E --> G[Log Analysis]
    G --> H[LogBERT Anomaly Detection]
    G --> I[Multi-Service Correlation]

    H --> J[Incident Intelligence]
    I --> J

    J --> K[Evidence-Grounded RCA]
    K --> L[Operator Review]

    L --> F
    F --> M[FastAPI Backend]
    M --> N[Next.js Dashboard]
```

### Main Components

| Component | Responsibility |
|---|---|
| Next.js Frontend | Dashboard, logs, incidents, investigation and reports |
| FastAPI Backend | API endpoints and application logic |
| Parser Pipeline | Format detection, parsing and normalization |
| PostgreSQL | Relational application data and incident records |
| OpenSearch | Log indexing, searching and aggregations |
| LogBERT | HDFS log-sequence anomaly detection |
| Correlation Engine | Multi-service event correlation |
| RCA Engine | Evidence-supported diagnostic hypotheses |

---

## 4. Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Frontend | Next.js 16 | Web application |
| Frontend Language | TypeScript | Type-safe application development |
| Styling | Tailwind CSS | Responsive interface |
| Charts | Recharts | Dashboard visualizations |
| Backend | FastAPI | REST API |
| Backend Language | Python 3.12 | Application and ML logic |
| ORM | SQLAlchemy 2.0 | Relational database access |
| Migrations | Alembic | Database schema migrations |
| Database | PostgreSQL 16 | Incidents, evidence and application records |
| Search Engine | OpenSearch 2.x | Log indexing and search |
| Machine Learning | PyTorch | LogBERT inference |
| NLP | Sentence Transformers | Semantic representations and similarity |

---

## 5. Prerequisites

Install the following before running RootLens:

- Python 3.12+
- Node.js 20.x or 22.x
- PostgreSQL 16+
- Docker Desktop (recommended for OpenSearch)
- Git 2.x+

---

## 6. Installation and Configuration

### A. Clone the Repository

```bash
git clone https://github.com/manas-dalvi/RootLens.git
cd RootLens
```

### B. Environment Configuration

Create a local environment file using the provided template:

**Windows PowerShell:**

```powershell
Copy-Item .env.example .env
```

**Linux/macOS:**

```bash
cp .env.example .env
```

Configure the required local settings in `.env`:

```ini
# Backend
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
ENVIRONMENT=development
DEBUG=True
SECRET_KEY=change_this_to_a_secure_random_secret
ALLOW_DEV_TOKENS=True

# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=rootlens
POSTGRES_USER=rootlens_user
POSTGRES_PASSWORD=your_secure_password
DATABASE_URL=postgresql://rootlens_user:your_secure_password@localhost:5432/rootlens

# OpenSearch
OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9200
OPENSEARCH_USE_SSL=false
OPENSEARCH_VERIFY_CERTS=false
OPENSEARCH_INDEX_PREFIX=rootlens-logs

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
```

Never commit real credentials, API keys, or local environment files.

### C. Backend Setup

Create and activate a virtual environment.

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux/macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r backend/requirements.txt
```

### D. PostgreSQL Setup

Create the application database and user:

```sql
CREATE USER rootlens_user WITH PASSWORD 'your_secure_password';
CREATE DATABASE rootlens OWNER rootlens_user;
GRANT ALL PRIVILEGES ON DATABASE rootlens TO rootlens_user;
```

Apply database migrations:

```bash
alembic -c backend/alembic.ini upgrade head
```

### E. OpenSearch Setup

Start a local single-node OpenSearch instance:

```bash
docker run -d \
  -p 9200:9200 -p 9600:9600 \
  -e "discovery.type=single-node" \
  -e "plugins.security.disabled=true" \
  --name rootlens-opensearch \
  opensearchproject/opensearch:2
```

This is a local development configuration. Production deployments require appropriate security and cluster configuration.

### F. Frontend Setup

```bash
cd frontend
npm install
```

---

## 7. Supported Log Formats and Parsers

RootLens uses an extensible parser architecture.

| Format | Parser | Typical Source |
|---|---|---|
| `hdfs` | HDFSLogParser | Hadoop Distributed File System |
| `json` | JSONLogParser | Structured microservice logs |
| `syslog` | SyslogParser | Linux systems and network devices |
| `application_log` | ApplicationLogParser | Python, Logback and Log4j logs |
| `csv` | CSVLogParser | Batch log exports |

### HDFS Parser

The HDFS parser:

- Recognizes HDFS raw log headers.
- Extracts block identifiers.
- Maps recognized messages to HDFS event templates E1–E29.
- Normalizes timestamps.
- Groups events into block-level sequences.
- Quarantines malformed or unrecognized records with explicit reasons.

HDFS sequence processing is designed for the LogBERT benchmark integration.

---

## 8. LogBERT Anomaly Detection

RootLens integrates a PyTorch-based LogBERT model for HDFS log-sequence anomaly detection.

### Model Artifacts

The full benchmark model artifacts are stored under:

```text
data/benchmarks/hdfs_full/
├── model/
│   ├── logbert_best.pt
│   ├── config.json
│   └── training_metadata.json
└── processed/
    ├── vocab.json
    ├── vocab_canonical.json
    └── preprocessing_metadata.json
```

The activated full-benchmark checkpoint is:

```text
data/benchmarks/hdfs_full/model/logbert_best.pt
```

Vocabulary:

```text
data/benchmarks/hdfs_full/processed/vocab.json
```

Decision threshold:

```text
0.0004
```

The packaged checkpoint and vocabulary are used for inference. Full dataset download and model retraining are not required for normal application startup when these artifacts are present.

### Full HDFS Benchmark Dataset

The HDFS benchmark dataset is available through Zenodo:

- **DOI:** [10.5281/zenodo.8196385](https://doi.org/10.5281/zenodo.8196385)
- **Archive:** `HDFS_v1.zip`
- **MD5:** `76a24b4d9a6164d543fb275f89773260`
- **License:** CC-BY 4.0

Benchmark characteristics:

- 11,175,629 raw log lines.
- 575,061 block-level sessions.
- 29 event templates.

### Dataset Reproduction

To reproduce the full benchmark:

1. Download `HDFS_v1.zip` from the Zenodo source.
2. Verify the downloaded archive using the MD5 checksum above.
3. Extract the dataset into:

   ```text
   data/benchmarks/hdfs_full/raw/
   ```

4. Use the repository's HDFS preprocessing pipeline to generate the processed session files and vocabulary.
5. Recreate the training, validation, and held-out test splits using the documented benchmark configuration.
6. Run the benchmark training and evaluation pipeline.
7. Compare the generated evaluation report with the published benchmark results in Section 13.

**Important:** The full raw dataset and large generated session files are not required for ordinary inference when the packaged model artifacts are available. The dataset should be downloaded separately when reproducing the full training and evaluation process.

The exact preprocessing, training, and evaluation script entry points must be taken from the repository's implementation rather than assumed from this documentation.

---

## 9. Multi-Service Correlation and Evidence-Grounded RCA

RootLens correlates events using multiple relationship types:

| Correlation Type | Description |
|---|---|
| `TRACE_BASED` | Events sharing the same distributed trace identifier |
| `SPAN_BASED` | Parent-child span relationships |
| `REQUEST_BASED` | Shared request or correlation identifiers |
| `SERVICE_LOCAL` | Related events within one service |
| `TEMPORAL_CASCADE` | Events occurring within a configured temporal window |

### Evidence-Grounded RCA

RootLens follows an evidence-first approach:

- RCA hypotheses must reference supporting telemetry.
- Evidence links allow operators to inspect the underlying records.
- Correlation is treated as diagnostic evidence, not automatic proof of causality.
- AI-generated hypotheses cannot independently confirm root causes.

### Human-in-the-Loop Decision Safety

Only authorized operators can confirm or reject a root-cause hypothesis.

The system records operator decisions in its audit workflow. Automated AI confirmation is not permitted.

---

## 10. Running the Application

### A. Start the Backend

With the virtual environment activated:

```bash
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

Backend:

```text
http://127.0.0.1:8000
```

API documentation:

```text
http://127.0.0.1:8000/docs
```

### B. Start the Frontend

Open a separate terminal:

```bash
cd frontend
npm run dev
```

Frontend:

```text
http://localhost:3000
```

---

## 11. Running Tests and Verification

### Backend Tests

**Windows PowerShell:**

```powershell
$env:PYTHONPATH="backend"
pytest backend/tests -v
```

**Linux/macOS:**

```bash
PYTHONPATH=backend pytest backend/tests -v
```

Latest reported backend test result:

```text
566 passed, 3 warnings
```

Test results can change when the implementation or environment changes. Run the test suite again to verify the current checkout.

### Frontend Contract Tests

From the repository root:

```bash
node --test tests/*.mjs
```

### Static Type Checking

```bash
npx pyright --pythonpath .venv/Scripts/python.exe backend/
```

### Frontend Linting and Production Build

```bash
cd frontend
npm run lint
npm run build
```

---

## 12. Demonstration Walkthrough

A typical multi-service incident investigation can be demonstrated using a sample database connection cascade scenario.

1. Start PostgreSQL and OpenSearch.
2. Start the FastAPI backend.
3. Start the Next.js frontend.
4. Open `http://localhost:3000`.
5. Navigate to the Log Upload page.
6. Upload the available sample scenario dataset.
7. Allow the backend to parse, normalize, and index the records.
8. Navigate to the Incidents page.
9. Inspect the detected incident candidates.
10. Open the incident workspace.
11. Review the event timeline and supporting log evidence.
12. Inspect the RCA hypotheses and their evidence references.
13. Review diagnostic recommendations.
14. Use the operator review workflow to confirm or reject a hypothesis when the supporting evidence has been independently verified.
15. Export the incident report in a supported format.

Use the actual sample dataset and scenario available in the repository. Demonstration results may vary with the uploaded data and existing database state.

---

## 13. Evaluation and Verification Evidence

### LogBERT HDFS Benchmark

The full HDFS held-out test evaluation contains:

| Metric | Result |
|---|---:|
| Total test sessions | 64,242 |
| Anomalous sessions | 8,419 |
| Normal sessions | 55,823 |
| Precision | 97.45% |
| Recall | 52.24% |
| F1 Score | 68.02% |
| Accuracy | 93.56% |
| False Positive Rate | 0.21% |

The benchmark uses a held-out test split. The reported threshold is `0.0004`.

**Interpretation:** The model achieves high precision and a low false-positive rate, but its recall indicates that some anomalous sessions are not detected.

Full report:

`docs/benchmark-evaluation-report.md`

### Multi-Service RCA Evaluation

The RCA evaluation report documents synthetic multi-service failure scenarios and evidence-retrieval results.

Reported results:

- Top-1 Accuracy (Classifiable): 100% (11/11).
- Recall@K Evidence Retrieval: 100% (11/11).
- Citation Validity: 100% (84/84).
- False Confirmation Rate: 0% (0/12).

Full report:

`docs/rca_evaluation_report.md`

### Integration Verification

The integration verification report documents lifecycle and security checks.

Full report:

`docs/phase11_integration_verification_report.md`

---

## 14. Known Limitations

- **HDFS Domain Specialization:** The packaged LogBERT model is calibrated for HDFS log-event sequences. It should not be treated as a universal anomaly detector for every microservice log format.
- **Anomaly Detection vs Root Cause:** LogBERT detects potentially anomalous sequences; it does not independently establish the root cause of an incident.
- **Recall:** The reported benchmark recall is 52.24%, meaning some anomalous sessions remain undetected.
- **OpenSearch Deployment:** The documented single-node setup is intended for local development, not production-scale deployment.
- **Hardware:** Large-scale training and backfills may benefit from GPU acceleration.
- **Human Review:** Root-cause confirmation remains an operator-controlled decision.

---

## 15. Troubleshooting

| Issue | Possible Cause | Resolution |
|---|---|---|
| PostgreSQL connection refused | Database service stopped or incorrect credentials | Verify PostgreSQL service and `DATABASE_URL` |
| OpenSearch unavailable | Docker container stopped | Run `docker start rootlens-opensearch` |
| Alembic migration error | Database schema not updated | Run `alembic -c backend/alembic.ini upgrade head` |
| CORS error | Frontend origin not configured | Check backend CORS settings |
| JWT unauthorized | Missing or invalid authorization token | Verify authentication and development configuration |
| LogBERT artifact unavailable | Missing checkpoint or vocabulary | Verify the `hdfs_full/model` and `hdfs_full/processed` paths |

---

## License and Dataset Attribution

The HDFS benchmark dataset is distributed under its stated CC-BY 4.0 license. Refer to the original dataset source for attribution requirements.

RootLens is an academic and engineering project focused on enterprise log observability, anomaly detection, multi-service correlation, and evidence-grounded root cause
