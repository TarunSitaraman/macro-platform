# Macro Intelligence Platform

An agentic macroeconomic data intelligence platform. Ingests, validates, forecasts, and serves macroeconomic indicator data through a Medallion architecture. Features a multi-tenant enterprise backend, a RAG-powered chatbot, and an autonomous research agent for comprehensive reporting.

## 🏗️ Architecture

The platform has evolved from a basic prototype into a production-ready enterprise system.

*   **Multi-Tenant Security Core**: 
    *   JWT-based authentication with Role-Based Access Control (RBAC: Admin, Analyst, Viewer).
    *   Strict database-level row isolation via `tenant_id` across all data layers, chat histories, and audit logs.
*   **Orchestration (Dagster)**:
    *   Data pipelines are defined as **Software-Defined Assets (SDA)** in a DAG structure.
    *   Handles automated scheduling, retries, and data lineage tracking.
*   **Medallion Data Pipeline**:
    *   **Bronze**: Raw, append-only records from APIs (World Bank, IMF, FRED) and dynamic web crawlers.
    *   **Silver**: Cleaned data with composite Data Quality (DQ) scores. Records `<90%` require human-in-the-loop review.
    *   **Gold**: Production-ready data with **pgvector embeddings** (Jina AI) for RAG.
*   **Predictive Analytics (Prophet)**:
    *   Automated time-series forecasting generates forward-looking data points.
    *   Trend-based **Anomaly Detection** flags outliers outside the 99% confidence interval.
*   **Agentic Intelligence**:
    *   **ChatbotAgent**: RAG-powered Q&A with strict guardrails and citation enforcement.
    *   **AlertAgent**: Monitors the Gold layer for critical macro signals (e.g., high inflation, negative growth).
    *   **ResearcherAgent**: Combines real-time web search (DuckDuckGo) with internal data to synthesize and export professional PDF research reports using Gemini.
*   **Observability**: Integrated OpenTelemetry (OTLP) tracing across FastAPI routes and SQLAlchemy queries.

---

## 🚀 Quick Start

### 1. Requirements

- Python 3.12+
- PostgreSQL 15+ with the `pgvector` extension.
- API Keys: Jina AI (Embeddings), Groq/Gemini/OpenRouter (LLMs), FRED (Data).

### 2. Installation

```bash
# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Mac/Linux

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers (for the Web Crawler)
playwright install
```

### 3. Environment Configuration

Copy `.env.example` to `.env` and fill in your keys:

```env
APP_ENV=development
DATABASE_URL=postgresql://user:pass@localhost:5432/macro_db
JINA_API_KEY=your_key
GEMINI_API_KEY=your_key
GROQ_API_KEY=your_key
API_SECRET_KEY=your_secure_jwt_secret
```

### 4. Database Setup

Initialise the database, create tables, and seed the global indicators:

```bash
python db_init.py

# Create the default Admin user
python register_admin.py
```
*(Default login: `admin@example.com` / `admin123`)*

### 5. Running the Platform

The platform consists of three main services. Run them in separate terminal windows:

**1. FastAPI Backend:**
```bash
uvicorn src.api.main:app --reload
```
*(Available at http://localhost:8000)*

**2. Dagster Orchestrator:**
```bash
dagster dev -f src/orchestration/jobs.py
```
*(Available at http://localhost:3000)*

**3. Streamlit Frontend:**
```bash
streamlit run src/ui/app.py
```
*(Available at http://localhost:8501)*

---

## 📂 Project Structure

```text
├── src/
│   ├── agents/          # AI Agents (Chatbot, Researcher, Forecaster, Crawler)
│   ├── api/             # FastAPI REST endpoints
│   ├── orchestration/   # Dagster jobs, assets, and resources
│   ├── ui/              # Streamlit frontend & dashboards
│   └── utils/           # Auth, Observability, and PDF Reporting helpers
├── db_init.py           # Database schema & seed script
├── register_admin.py    # Setup script for default tenant/admin
├── pyproject.toml       # Linter/formatter configs
└── requirements.txt     # Python dependencies
```

## 📜 License

MIT
