"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

import nest_asyncio
nest_asyncio.apply()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.config import get_settings
from src.database import engine, init_db
from src.api.routes import audit, auth, chat, data, pipelines, review
from src.utils.observability import setup_observability
from trust import TrustLayer
from trust.database.migrations import create_trust_tables

logging.basicConfig(level=get_settings().log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Macro Intelligence Platform API")
    init_db()
    create_trust_tables()
    yield
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Macro Intelligence Platform API",
        description="Macroeconomic data intelligence platform — REST API",
        version="1.0.0",
        lifespan=lifespan,
    )

    # setup_observability must be called before adding other middleware or routes
    # as it instruments the app by adding middleware.
    setup_observability(app, engine)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(data.router, prefix="/api/v1", tags=["Data"])
    app.include_router(pipelines.router, prefix="/api/v1", tags=["Pipelines"])
    app.include_router(review.router, prefix="/api/v1", tags=["Review Queue"])
    app.include_router(chat.router, prefix="/api/v1", tags=["Chatbot"])
    app.include_router(audit.router, prefix="/api/v1", tags=["Audit & Lineage"])

    # Mount all nine trust pillars (middleware + routers)
    TrustLayer.mount(app)

    @app.get("/health", tags=["Health"])
    async def health():
        return {"status": "ok", "env": settings.app_env}

    # Mount static files for JS dashboard
    app.mount("/", StaticFiles(directory="src/ui/static", html=True), name="static")

    @app.exception_handler(Exception)
    async def global_exc_handler(request, exc):
        logger.error("Unhandled error: %s", exc, exc_info=True)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
