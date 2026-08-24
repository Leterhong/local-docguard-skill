"""
Agent orchestration API.

POST /api/agent/run  -> autonomous multi-step run: the local LLM plans a
tool sequence (analyze / bid check / compare / RAG search), executes it,
and returns the final answer plus a full step-by-step trace.

This endpoint is the "Skills calling Skills" surface: the orchestrator
composes the same tools that other Skills call individually, and it is
itself callable by any external Skill / Agent over HTTP.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from server.models.schemas import AgentRunRequest, StandardResponse
from server.services.container import get_container
from server.services.orchestrator import Orchestrator
from server.services.security import get_logger

logger = get_logger("api.agent")
router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.post("/run", response_model=StandardResponse)
async def agent_run(req: AgentRunRequest):
    if not req.goal or not req.goal.strip():
        raise HTTPException(status_code=422, detail="goal must not be empty")
    if not req.file_paths and not req.question.strip():
        raise HTTPException(
            status_code=422,
            detail="Provide at least one file_path or a question.",
        )

    container = get_container()
    orchestrator = Orchestrator(container)
    try:
        result = orchestrator.run(
            goal=req.goal.strip(),
            file_paths=req.file_paths,
            question=req.question,
            profile_text=req.profile_text,
            doc_type_hint=req.doc_type_hint.value if req.doc_type_hint else None,
            use_llm=req.use_llm,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent run failed")
        raise HTTPException(status_code=500, detail=f"Agent run failed: {exc}")

    return StandardResponse(
        success=True,
        message="Agent run complete",
        data=result.to_dict(),
    )
