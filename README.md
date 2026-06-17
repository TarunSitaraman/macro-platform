# Macro Intelligence Platform

An agentic macroeconomic data intelligence platform. Ingests, validates, forecasts, and serves macroeconomic indicator data through a Medallion architecture. Features a multi-tenant enterprise backend, a RAG-powered chatbot, and an autonomous research agent for comprehensive reporting.

## 🏗️ Architecture

The platform is built on a robust enterprise-grade foundation:

*   **Multi-Tenant Security Core**: 
    *   JWT-based authentication with Role-Based Access Control (RBAC: Admin, Analyst, Viewer).
    *   Strict database-level row isolation via `tenant_id` across all data layers, chat histories, and audit logs.
*   **Agent Orchestration Framework**:
    *   Custom **AgentOrchestrator** with tool-augmented execution and multi-step reasoning.
    *   **ResponseVerifier** for grounding checks and citation enforcement to prevent hallucinations.
    *   Tool registry including vector search, timeseries analysis, and cross-country comparisons.
*   **DQ Trust & Lineage**:
    *   Human-readable **Trust Explanations** detailing how DQ scores are calculated (Accuracy, Completeness, Timeliness, Consistency).
    *   Full data provenance from Bronze (Raw) to Gold (Production) via automated lineage tracking.
*   **Medallion Data Pipeline (Dagster)**:
    *   **Bronze**: Raw records from World Bank, IMF, FRED, and dynamic LLM-augmented web crawlers.
    *   **Silver**: Cleaned data with composite DQ scoring. Records `<90%` go to a **Human-in-the-Loop Review Queue**.
    *   **Gold**: Production data with **pgvector embeddings** (Jina AI / Gemini) for semantic RAG.
*   **Predictive Analytics (Prophet)**:
    *   Automated time-series forecasting generates forward-looking estimates.
    *   Outlier detection flags records outside the 99% confidence interval.

---

## 📊 Key Features

*   **Data Explorer**: High-performance filtering of 75,000+ records. Includes 2025+ coverage, forecast toggles, SI unit formatting, and dashed-line forecasting on charts.
*   **Macro AI Chatbot**: Multi-turn conversation with citations, tool use, and safety guardrails against investment advice.
*   **Summary Engine**: AI-generated country snapshots and indicator briefs with dynamic chart generation.
*   **Autonomous Researcher**: Compiles professional deep-dive research reports (web search + internal data) into PDF format.
*   **Anomaly Alerts**: Monitoring system for critical macro signals (e.g., negative growth, hyperinflation).

---

## 🚀 Quick Start

### 1. Requirements

- Python 3.12+
- PostgreSQL 15+ with the `pgvector` extension.
- API Keys: Jina AI/Gemini (Embeddings), Groq/Gemini/OpenRouter (LLMs), FRED (Data).

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

Run services in separate terminals:

**1. FastAPI Backend:** `uvicorn src.api.main:app --reload` (http://localhost:8000)
**2. Dagster Orchestrator:** `dagster dev -f src/orchestration/jobs.py` (http://localhost:3000)
**3. Streamlit Frontend:** `streamlit run src/ui/app.py` (http://localhost:8501)

---

## 📂 Project Structure

```text
├── src/
│   ├── agents/          # Agent Framework (Orchestrator, Chatbot, Tools)
│   ├── api/             # FastAPI REST endpoints & Routers
│   ├── orchestration/   # Dagster jobs, assets, and resources
│   ├── ui/              # Streamlit frontend, pages, & static assets
│   └── utils/           # DQ Explain, Auth, and Reporting helpers
├── migrations/          # SQL migration scripts
└── tests/               # Comprehensive unit and integration tests
```

## 📜 License

MIT
