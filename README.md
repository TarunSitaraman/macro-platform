# Hexaware Macro Intelligence Platform

An agentic macroeconomic data intelligence platform built for Hexaware's Financial Services Practice. Ingests, validates, and serves macroeconomic indicator data through a medallion architecture, with a RAG-powered chatbot and multi-provider LLM routing.

---

## What It Does

The platform continuously pulls macroeconomic data from public APIs and web sources, runs it through a Bronze → Silver → Gold quality pipeline, stores production-ready records with vector embeddings in PostgreSQL, and surfaces everything through a Streamlit UI and FastAPI backend.

**Indicators tracked:** GDP (current USD), GDP growth rate, CPI inflation, unemployment rate, current account balance (% GDP), government debt (% GDP)

**Countries covered (Phase 1):** USA, GBR, DEU, FRA, JPN, CHN, IND, BRA, CAN, AUS, KOR, MEX, ITA, ESP, NLD, SAU, ZAF, ARG, IDN, TUR

---

## Architecture

```
External Sources          Bronze Layer          Silver Layer          Gold Layer
─────────────────        ─────────────         ─────────────         ──────────────
World Bank API    ──▶    Raw records   ──▶     Cleaned &     ──▶     Production     ──▶  Chatbot RAG
IMF WEO API              (append-only)         DQ-scored             records with         Dashboards
FRED API                 Full audit trail      DQ ≥ 90% → Auto      embeddings           REST API
Web crawlers                                   70–90%  → Review      pgvector index       Summaries
                                               < 70%   → Reject
```

### Data Quality Scoring

Every record receives a composite DQ score (0–100) based on four sub-scores:

| Sub-score | Weight | What it checks |
|-----------|--------|----------------|
| Accuracy | 40% | Value parseable, within plausible range, unit matches |
| Completeness | 30% | No nulls, country/period/source all present |
| Timeliness | 20% | Data freshness relative to crawl time |
| Consistency | 10% | Matches expected frequency and source reputation |

- **≥ 90%** → Auto-promoted to Gold
- **70–90%** → Queued for human review (4-hour SLA)
- **< 70%** → Rejected with failure reasons logged

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| UI | Streamlit (multipage) |
| Backend API | FastAPI + Uvicorn |
| Database | Neon PostgreSQL + pgvector |
| ORM | SQLAlchemy 2.0 |
| Embeddings | Jina AI (`jina-embeddings-v3`, 1024-dim, 1M free tokens/month) |
| LLM — Primary | Groq (`llama-3.3-70b-versatile`, 14,400 req/day free) |
| LLM — Fallback | Google Gemini (`gemini-2.0-flash`) |
| LLM — Last resort | OpenRouter (free-tier models) |
| Static data | World Bank API, IMF WEO API, FRED API |
| Web crawling | Crawl4AI + Playwright |
| Deployment | Render (API + UI), Neon (DB) |

---

## Project Structure

```
hexaware-macro-platform/
├── src/
│   ├── agents/
│   │   ├── pipeline.py      # Bronze→Silver→Gold orchestration
│   │   ├── static.py        # WorldBank / IMF / FRED API clients
│   │   ├── crawler.py       # Crawl4AI web extraction
│   │   ├── chatbot.py       # RAG chatbot with citation enforcement
│   │   ├── embeddings.py    # Jina AI embedding client
│   │   ├── llm_client.py    # Multi-provider LLM with fallback routing
│   │   ├── qa.py            # DQ scoring engine
│   │   └── summarizer.py    # Macro summary generation
│   ├── api/
│   │   ├── main.py          # FastAPI app
│   │   └── routes/          # data, pipelines, review, chat, audit
│   ├── ui/
│   │   ├── app.py           # Streamlit entry point
│   │   └── _pages/          # 7 pages (overview, static data, crawler, explorer, review, chatbot, summaries)
│   ├── config.py            # Pydantic settings + model routing config
│   └── database.py          # SQLAlchemy models (Bronze, Silver, Gold, Review, Chat)
├── migrations/
│   └── init.sql             # Schema with pgvector extension
├── docker/                  # Dockerfiles for API and UI
├── tests/unit/              # Pytest unit tests
├── db_init.py               # One-shot schema creation + seed
├── setup.bat                # Windows dev setup script
├── run.bat                  # Launch Streamlit locally
├── render.yaml              # Render.com deployment config
└── .env.example             # Environment variable template
```

---

## Getting Started

### Prerequisites

- Python 3.12+
- A [Neon](https://neon.tech) PostgreSQL database (free tier)
- A [Groq](https://console.groq.com) API key (free, 14,400 req/day)
- A [Jina AI](https://jina.ai) API key (free, 1M tokens/month)
- A [FRED](https://fred.stlouisfed.org/docs/api/api_key.html) API key (free)

### 1. Clone and set up environment

```bash
git clone https://github.com/TarunSitaraman/hexaware-macro-platform.git
cd hexaware-macro-platform
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```env
DATABASE_URL=postgresql://user:password@ep-xxx.neon.tech/dbname?sslmode=require
GROQ_API_KEY=gsk_...
GEMINI_API_KEY=AQ....          # optional fallback
OPENROUTER_API_KEY=sk-or-...   # optional fallback
JINA_API_KEY=jina_...
FRED_API_KEY=your_fred_key
```

### 3. Initialise the database

```bash
python db_init.py
```

This creates all tables, enables the pgvector extension, and seeds the source registry.

### 4. Run the platform

```bash
run.bat          # Windows — launches Streamlit on http://localhost:8501
```

Or manually:

```bash
set PYTHONPATH=%CD%
streamlit run src/ui/app.py
```

---

## UI Pages

| Page | Description |
|------|-------------|
| Platform Overview | Live KPIs, medallion architecture diagram, source registry |
| Static Data Product | Trigger World Bank / IMF / FRED ingestion, generate embeddings |
| Dynamic Crawler | Run web crawlers against financial news sources |
| Data Explorer | Browse and filter gold records, download CSV |
| Review Queue | Human-in-the-loop approval for 70–90% DQ records |
| Chatbot | RAG-powered Q&A with citation enforcement and guardrails |
| Summary Engine | AI-generated macro summaries (country, indicator, global) |

---

## LLM Routing

The platform uses a three-tier routing system with automatic fallback:

```
simple  → intent classification, JSON extraction       → Groq → Gemini → OpenRouter
medium  → structured extraction, DQ rationale          → Groq → Gemini → OpenRouter
complex → RAG chat, summaries, multi-indicator reports → Groq → Groq (lite) → Gemini → OpenRouter
```

All providers are optional — the client skips any provider whose API key is not set and falls through to the next. If all fail, a clear error lists each failure reason.

---

## Chatbot Guardrails

The RAG chatbot enforces topic scope and citation discipline:

- Only answers questions about macroeconomic indicators
- Declines investment advice requests
- Cites every numeric claim as `[Source: <source_name>, <period>]`
- Falls back to most-recent records if no embeddings exist yet
- Maintains conversation history (last 20 turns) per session

---

## Data Sources

| Source | Coverage | Auth |
|--------|----------|------|
| World Bank Open Data | 20 countries × 6 indicators × multiple years | None (public) |
| IMF World Economic Outlook | Global, annual forecasts included | None (public) |
| FRED (US Federal Reserve) | US macroeconomic series | Free API key |
| Web crawlers | IMF Blog, World Bank Blog (HTML extraction) | None |

---

## Deployment (Render + Neon)

1. Push this repo to GitHub
2. Create a [Render](https://render.com) account and connect the repo
3. Render auto-detects `render.yaml` and creates two services: API + UI
4. Set environment variables in the Render dashboard (never commit `.env`)
5. The Neon database connection string goes in `DATABASE_URL`

---

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | Neon PostgreSQL connection string |
| `GROQ_API_KEY` | Recommended | Primary LLM provider (free) |
| `GEMINI_API_KEY` | Optional | Fallback LLM (Google AI Studio) |
| `OPENROUTER_API_KEY` | Optional | Last-resort LLM fallback |
| `JINA_API_KEY` | Yes | Embeddings for RAG search |
| `FRED_API_KEY` | Yes | US Federal Reserve data |
| `DQ_AUTO_PROMOTE_THRESHOLD` | No | Default: 90 |
| `DQ_REVIEW_THRESHOLD` | No | Default: 70 |
| `REVIEW_SLA_HOURS` | No | Default: 4 |

---

## License

MIT
