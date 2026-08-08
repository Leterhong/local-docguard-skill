"""
DocGuard AI — FastAPI application entry point.

Run locally:
    python -m uvicorn server.main:app --host 127.0.0.1 --port 8765 --reload

Or:
    python -m server.main

The server binds to 127.0.0.1 only (localhost), so no document data ever
leaves the machine. It exposes:
  * /api/*            JSON API for Agent tools and the Demo UI
  * /api/analyze/stream  SSE progress stream
  * /                 Enterprise Demo UI (static)
  * /docs             OpenAPI / Swagger UI
"""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server import __version__
from server.api import analyze, compare, health, report, search
from server.config import get_settings
from server.services.container import get_container
from server.services.security import get_logger

logger = get_logger("main")
settings = get_settings()

app = FastAPI(
    title="DocGuard AI — 企业文档智能审查 Skill",
    description=(
        "本地运行的企业文档智能审查 Agent Skill：OCR + RAG + 文档理解 + "
        "风险分析 + 报告生成。所有模型与文件均在 localhost 运行，不上传云端。"
    ),
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — restricted to localhost origins defined in config.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers.
app.include_router(health.router)
app.include_router(analyze.router)
app.include_router(search.router)
app.include_router(report.router)
app.include_router(compare.router)


# ----------------------------------------------------------------------
# Demo UI (static)
# ----------------------------------------------------------------------
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        index_file = WEB_DIR / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return {"message": "DocGuard AI API running", "docs": "/docs"}
else:
    @app.get("/", include_in_schema=False)
    async def index():
        return {"message": "DocGuard AI API running", "docs": "/docs"}


# ----------------------------------------------------------------------
# Startup / shutdown
# ----------------------------------------------------------------------
@app.on_event("startup")
def _on_startup():
    logger.info("DocGuard AI %s starting on %s:%s", __version__, settings.host(), settings.port())
    logger.info("Local-only mode: %s", settings.is_local_only())

    # Warm up services in a background thread so the server responds
    # immediately while models load (LLM loading can take time).
    def _warmup():
        try:
            container = get_container()
            logger.info("Initializing embedding service...")
            _ = container.embedder
            logger.info("Embedding: %s (available=%s)",
                        container.embedder.loaded_name, container.embedder.available)
            logger.info("Initializing LLM service...")
            _ = container.llm
            if container.llm.available:
                logger.info("LLM ready: %s on %s",
                            container.llm.loaded_name, container.llm.device)
            else:
                logger.warning("LLM not loaded; rule-based analysis will be used.")
            _ = container.vector_store
            logger.info("Vector store: %d documents, %d chunks indexed.",
                        container.vector_store.document_count(),
                        container.vector_store.chunk_count())
        except Exception as exc:  # noqa: BLE001
            logger.exception("Warmup failed: %s", exc)

    threading.Thread(target=_warmup, daemon=True).start()


@app.on_event("shutdown")
def _on_shutdown():
    logger.info("DocGuard AI shutting down.")


def main():
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=settings.host(),
        port=settings.port(),
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
