"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import get_settings
from src.database import init_db
from src.api.routes import audit, chat, data, pipelines, review

logging.basicConfig(level=get_settings().log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Hexaware Macro Platform API")
    init_db()
    yield
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Hexaware Macro Platform API",
        description="Macroeconomic data intelligence platform — REST API",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(data.router, prefix="/api/v1", tags=["Data"])
    app.include_router(pipelines.router, prefix="/api/v1", tags=["Pipelines"])
    app.include_router(review.router, prefix="/api/v1", tags=["Review Queue"])
    app.include_router(chat.router, prefix="/api/v1", tags=["Chatbot"])
    app.include_router(audit.router, prefix="/api/v1", tags=["Audit & Lineage"])

    @app.get("/health", tags=["Health"])
    async def health():
        return {"status": "ok", "env": settings.app_env}

    @app.exception_handler(Exception)
    async def global_exc_handler(request, exc):
        logger.error("Unhandled error: %s", exc, exc_info=True)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
