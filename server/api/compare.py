"""
Compare API — diff two document versions.

POST /api/compare  -> structured comparison with change summary
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from server.config import get_settings
from server.models.schemas import CompareRequest, StandardResponse
from server.services.container import get_container
from server.services.document_compare import compare_documents
from server.services.security import get_logger, resolve_input_file

logger = get_logger("api.compare")
router = APIRouter(prefix="/api", tags=["compare"])
settings = get_settings()


@router.post("/compare", response_model=StandardResponse)
async def compare(req: CompareRequest):
    try:
        path_a = resolve_input_file(req.file_path_a, settings)
        path_b = resolve_input_file(req.file_path_b, settings)
    except (FileNotFoundError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    container = get_container()
    try:
        result = compare_documents(
            path_a, path_b,
            ocr_service=container.ocr if container.ocr.available else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Comparison failed")
        raise HTTPException(status_code=500, detail=f"Comparison failed: {exc}")

    return StandardResponse(success=True, data=result.model_dump(mode="json"))
