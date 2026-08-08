"""
Search API — RAG-based document Q&A.

POST /api/search  -> retrieve relevant chunks and generate an answer
                     (using the local LLM when available)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from server.models.schemas import SearchRequest, StandardResponse
from server.services.container import get_container
from server.services.security import get_logger

logger = get_logger("api.search")
router = APIRouter(prefix="/api", tags=["search"])


@router.post("/search", response_model=StandardResponse)
async def search(req: SearchRequest):
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")

    container = get_container()
    top_k = req.top_k or int(container.settings.processing.get("top_k", 6))

    try:
        result = container.engine.search(
            query=req.query.strip(),
            document_id=req.document_id,
            top_k=top_k,
            use_cloud=req.use_cloud,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Search failed")
        raise HTTPException(status_code=500, detail=f"Search failed: {exc}")

    return StandardResponse(success=True, data=result.model_dump(mode="json"))
