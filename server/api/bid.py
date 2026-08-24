"""
Bid qualification check API.

POST /api/bid/check  -> tender requirements vs bidder qualifications,
                        returning a go/no-go verdict, per-item match,
                        score, and blocking gaps.

The bidder profile can be supplied as free text and/or a local file
(.txt/.md/.docx/.pdf). The local LLM, when available, upgrades
"uncertain" deterministic matches with semantic judgement.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException

from server.api.analyze import _analysis_cache
from server.config import get_settings
from server.models.schemas import BidCheckRequest, DocumentType, RequirementItem, StandardResponse
from server.services.bid_matcher import BidMatcher
from server.services.container import get_container
from server.services.document_parser import parse_document
from server.services.rules_engine import TenderRuleEngine
from server.services.security import get_logger, resolve_input_file

logger = get_logger("api.bid")
router = APIRouter(prefix="/api/bid", tags=["bid"])
settings = get_settings()


def _load_profile_text(profile_text: str, profile_file: str | None, user_id: str) -> str:
    parts: List[str] = []
    if profile_text and profile_text.strip():
        parts.append(profile_text.strip())
    if profile_file:
        try:
            path = resolve_input_file(profile_file, settings, user_id)
            doc = parse_document(path)
            if doc.full_text.strip():
                parts.append(doc.full_text.strip())
        except (FileNotFoundError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=f"Cannot read profile file: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to parse profile file %s: %s", profile_file, exc)
    return "\n\n".join(parts)


def _extract_requirements_from_file(path: Path) -> List[RequirementItem]:
    doc = parse_document(path)
    tender = TenderRuleEngine()
    return tender.extract_requirements(doc.full_text)


@router.post("/check", response_model=StandardResponse)
async def check_bid(req: BidCheckRequest):
    container = get_container()

    # 1) obtain the tender requirements
    requirements: List[RequirementItem] = []
    tender_name = ""
    if req.document_id:
        cached = _analysis_cache.get(req.document_id)
        if not cached:
            raise HTTPException(status_code=404, detail="Tender analysis not found or expired; analyze the tender first.")
        requirements = list(cached.requirements)
        tender_name = cached.file_name
    elif req.file_path:
        try:
            path = resolve_input_file(req.file_path, settings, req.user_id)
        except (FileNotFoundError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        requirements = _extract_requirements_from_file(path)
        tender_name = path.name
    else:
        raise HTTPException(status_code=422, detail="Provide either file_path (tender) or document_id.")

    if not requirements:
        raise HTTPException(status_code=422, detail="No qualification requirements were extracted from the tender.")

    # 2) load the bidder's own qualifications
    profile = _load_profile_text(req.profile_text, req.profile_file, req.user_id)
    if not profile.strip():
        raise HTTPException(
            status_code=422,
            detail="Provide the bidder qualifications via profile_text and/or profile_file.",
        )

    # 3) run matching (LLM only when available AND requested AND not cloud-forced-off)
    llm = container.llm if req.use_llm else None
    if req.use_cloud:
        # honor local-only lock: never use cloud if locked
        if llm is not None and settings.is_local_only():
            llm = container.llm  # stays local
    matcher = BidMatcher(llm_service=llm)
    result = matcher.evaluate(requirements, profile)
    result["tender_name"] = tender_name

    return StandardResponse(success=True, message="Bid qualification check complete", data=result)
