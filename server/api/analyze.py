"""
Analyze API — document analysis endpoint with real-time progress (SSE).

POST /api/analyze          -> start analysis, returns result JSON
POST /api/analyze/stream   -> Server-Sent Events stream of progress + final result
POST /api/upload           -> upload a file into the user sandbox, returns path
GET  /api/analysis/{doc_id}-> fetch a stored analysis (in-memory cache)
"""
from __future__ import annotations

import json
import queue
import shutil
import threading
import uuid
from pathlib import Path
from typing import Dict

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from server.config import get_settings
from server.models.schemas import (
    AnalysisStatus,
    AnalyzeRequest,
    DocumentAnalysis,
    ProgressEvent,
    StandardResponse,
)
from server.services.container import get_container
from server.services.security import (
    copy_to_sandbox,
    get_logger,
    resolve_input_file,
    safe_user_dir,
)

logger = get_logger("api.analyze")
router = APIRouter(prefix="/api", tags=["analyze"])

settings = get_settings()

# In-memory cache of recent analysis results (doc_id -> DocumentAnalysis).
_analysis_cache: Dict[str, DocumentAnalysis] = {}


@router.post("/upload", response_model=StandardResponse)
async def upload_file(file: UploadFile = File(...), user_id: str = "default"):
    """Upload a document into the user's isolated upload sandbox."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    suffix = Path(file.filename).suffix.lower()
    allowed = {".pdf", ".docx", ".txt", ".md", ".markdown", ".html", ".htm"}
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    upload_dir = safe_user_dir(settings, user_id, "uploads")
    dest = upload_dir / f"{uuid.uuid4().hex[:8]}_{Path(file.filename).name}"
    try:
        with dest.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")
    return StandardResponse(
        success=True,
        message="File uploaded",
        data={"file_path": str(dest), "file_name": dest.name},
    )


@router.post("/analyze", response_model=StandardResponse)
async def analyze(req: AnalyzeRequest):
    """Run full document analysis and return the structured result."""
    try:
        path = resolve_input_file(req.file_path, settings, req.user_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    container = get_container()
    try:
        result = container.engine.analyze(
            path,
            doc_type_hint=req.doc_type_hint,
            use_llm=req.use_llm,
            use_cloud=req.use_cloud,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")

    _analysis_cache[result.document_id] = result
    return StandardResponse(success=True, message="Analysis complete", data=result.to_public_dict())


@router.post("/analyze/stream")
async def analyze_stream(req: AnalyzeRequest):
    """Stream analysis progress as Server-Sent Events, ending with the result."""
    try:
        path = resolve_input_file(req.file_path, settings, req.user_id)
    except (FileNotFoundError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    container = get_container()
    event_queue: "queue.Queue[dict]" = queue.Queue()

    def on_progress(event: ProgressEvent):
        event_queue.put({"type": "progress", "data": event.model_dump(mode="json")})

    def worker():
        try:
            result = container.engine.analyze(
                path,
                doc_type_hint=req.doc_type_hint,
                use_llm=req.use_llm,
                use_cloud=req.use_cloud,
                progress=on_progress,
            )
            _analysis_cache[result.document_id] = result
            event_queue.put({"type": "result", "data": result.to_public_dict()})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Streaming analysis failed")
            event_queue.put({"type": "error", "data": str(exc)})
        finally:
            event_queue.put(None)  # sentinel

    threading.Thread(target=worker, daemon=True).start()

    def event_stream():
        while True:
            item = event_queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/analysis/{document_id}", response_model=StandardResponse)
async def get_analysis(document_id: str):
    result = _analysis_cache.get(document_id)
    if not result:
        raise HTTPException(status_code=404, detail="Analysis not found or expired")
    return StandardResponse(success=True, data=result.to_public_dict())


@router.get("/documents", response_model=StandardResponse)
async def list_documents():
    """List indexed documents and their chunk counts."""
    container = get_container()
    docs = container.vector_store.list_documents()
    return StandardResponse(
        success=True,
        data={"documents": [{"document_id": d, "chunks": c} for d, c in docs.items()]},
    )
