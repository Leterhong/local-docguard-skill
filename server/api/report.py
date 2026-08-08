"""
Report API — generate downloadable review reports.

POST /api/report  -> generate Markdown / HTML / JSON report from an
                     analysis result (or a stored analysis by document_id)
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from server.api.analyze import _analysis_cache
from server.config import get_settings
from server.models.schemas import DocumentAnalysis, ReportRequest, StandardResponse
from server.services.container import get_container
from server.services.report_generator import generate_report
from server.services.security import get_logger

logger = get_logger("api.report")
router = APIRouter(prefix="/api", tags=["report"])
settings = get_settings()


@router.post("/report", response_model=StandardResponse)
async def create_report(req: ReportRequest):
    data = req.analysis_result
    # Support passing just {"document_id": "..."} to use a cached result.
    if "document_id" in data and len(data) <= 3 and "risks" not in data:
        cached = _analysis_cache.get(data["document_id"])
        if not cached:
            raise HTTPException(status_code=404, detail="Analysis not found; run analyze first")
        analysis = cached
    else:
        try:
            analysis = DocumentAnalysis(**data)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=f"Invalid analysis_result: {exc}")

    container = get_container()
    report = generate_report(
        analysis,
        settings=container.settings,
        fmt=req.format,
        user_id=req.user_id,
    )
    return StandardResponse(
        success=True,
        message="Report generated",
        data={
            "report_id": report.report_id,
            "title": report.title,
            "format": report.format,
            "file_path": report.file_path,
            "download_url": f"/api/report/{report.report_id}/download",
        },
    )


@router.get("/report/{report_id}/download")
async def download_report(report_id: str):
    # Locate the report file by scanning the reports dir.
    reports_root = settings.reports_dir
    matches = list(reports_root.rglob(f"*_{report_id}.*"))
    if not matches:
        raise HTTPException(status_code=404, detail="Report not found or expired")
    path = matches[0]
    media_map = {
        ".html": "text/html; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".json": "application/json; charset=utf-8",
    }
    return FileResponse(
        path=str(path),
        media_type=media_map.get(path.suffix, "application/octet-stream"),
        filename=path.name,
    )
