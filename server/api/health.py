"""
Health API — service and model status.

GET /api/health  -> returns loaded model, device, embeddings, OCR status,
                    indexed document count. Used by the Demo UI's model
                    status panel.
GET /            -> simple service banner
"""
from __future__ import annotations

from fastapi import APIRouter

from server import __version__
from server.models.schemas import HealthStatus, StandardResponse
from server.services.container import get_container

router = APIRouter(tags=["health"])


@router.get("/api/health", response_model=HealthStatus)
async def health():
    container = get_container()
    h = container.health()
    return HealthStatus(
        status="ok",
        version=__version__,
        model_loaded=h["model_loaded"],
        model_name=h["model_name"],
        model_device=h["model_device"],
        embedding_loaded=h["embedding_loaded"],
        embedding_model=h["embedding_model"],
        ocr_available=h["ocr_available"],
        documents_indexed=h["documents_indexed"],
        provider=h.get("provider", "local"),
        cloud_available=h.get("cloud_available", False),
        cloud_enabled=h.get("cloud_enabled", False),
        local_only=h.get("local_only", True),
    )


@router.get("/api/providers", response_model=StandardResponse)
async def list_providers():
    """Return configured LLM providers and their availability."""
    container = get_container()
    return StandardResponse(
        success=True,
        data={"providers": container.llm.list_providers(), "current": container.llm.current_provider()},
    )


@router.get("/api", response_model=StandardResponse)
async def api_root():
    return StandardResponse(
        success=True,
        message="DocGuard AI local API. See /docs for OpenAPI schema.",
        data={"version": __version__},
    )
