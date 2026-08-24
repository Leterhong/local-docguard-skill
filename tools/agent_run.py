#!/usr/bin/env python3
"""
Tool: agent_run

Agent-facing entry point for autonomous multi-step document review. The
orchestrator (local LLM planner when available, deterministic planner
otherwise) chains the DocGuard tools itself -- analyze -> bid check /
version compare / RAG search -> final answer -- and returns the full
step-by-step trace as evidence of the agentic loop.

This is the "Skills calling Skills" surface: any external Agent or Skill
can invoke this tool, and the orchestrator internally composes the very
same tools (analyze_document / check_bid / compare_documents /
search_document) that those callers could also call individually.

Usage:
    # one contract: full review
    python tools/agent_run.py --file contract.pdf --goal "全面审查这份合同的风险"
    # tender + bidder profile: agent plans analyze -> bid self-check
    python tools/agent_run.py --file tender.docx --profile "注册资本5000万，ISO9001..." --goal "判断我方是否应投标"
    # two versions: agent plans analyze + compare
    python tools/agent_run.py --file v1.docx --file v2.docx --goal "对比两版合同的重大变化"
    # with an explicit question (adds RAG search to the plan)
    python tools/agent_run.py --file contract.pdf --question "违约金上限是多少" --goal "回答我的问题"
    # rule-based only (no LLM planning, deterministic pipeline)
    python tools/agent_run.py --file contract.pdf --goal "审查合同" --no-llm

Output:
    JSON agent_run result: answer, planner, llm_used, steps[] trace,
    and the artifacts produced by each tool call.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows-safe UTF-8 output (mandatory per local-ai-skill-authoring best practices).
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _client import ensure_running, post_json, print_json, upload_file  # noqa: E402


def agent_run(
    goal: str,
    file_paths: list[str] | None = None,
    question: str = "",
    profile_text: str = "",
    doc_type_hint: str | None = None,
    use_llm: bool = True,
    user_id: str = "default",
    auto_start: bool = True,
) -> dict:
    """Run the agentic orchestrator and return the full result with trace."""
    ensure_running(auto_start=auto_start)
    payload = {
        "goal": goal,
        "question": question,
        "profile_text": profile_text,
        "use_llm": use_llm,
        "user_id": user_id,
    }
    if doc_type_hint:
        payload["doc_type_hint"] = doc_type_hint
    if file_paths:
        payload["file_paths"] = [upload_file(p) for p in file_paths]

    resp = post_json("/api/agent/run", payload, timeout=1800)
    if not resp.get("success"):
        raise RuntimeError(f"Agent run failed: {resp.get('error') or resp.get('message')}")
    return resp["data"]


def main():
    parser = argparse.ArgumentParser(
        description=(
            "DocGuard AI - autonomous multi-step document review. "
            "The agent plans and chains analyze / bid-check / compare / RAG search itself."
        )
    )
    parser.add_argument("--goal", "-g", required=True, help="What you want done, in natural language")
    parser.add_argument("--file", "-f", action="append", default=[],
                        help="Document to review (repeatable: -f v1.docx -f v2.docx)")
    parser.add_argument("--question", "-q", default="", help="Explicit question for RAG search")
    parser.add_argument("--profile", "-p", default="", help="Bidder qualification text (for bid self-check)")
    parser.add_argument("--doc-type", default=None,
                        choices=["contract", "tender", "technical", "prd", "policy", "general"],
                        help="Optional document type hint")
    parser.add_argument("--no-llm", action="store_true", help="Deterministic pipeline, skip LLM planning")
    parser.add_argument("--user", default="default")
    parser.add_argument("--no-auto-start", action="store_true")
    args = parser.parse_args()

    for f in args.file:
        if not Path(f).exists():
            print(f"ERROR: file not found: {f}", file=sys.stderr)
            sys.exit(2)

    if not args.file and not args.question.strip():
        print("ERROR: provide at least one --file or a --question", file=sys.stderr)
        sys.exit(2)

    result = agent_run(
        goal=args.goal,
        file_paths=args.file,
        question=args.question,
        profile_text=args.profile,
        doc_type_hint=args.doc_type,
        use_llm=not args.no_llm,
        user_id=args.user,
        auto_start=not args.no_auto_start,
    )
    print_json(result)


if __name__ == "__main__":
    main()
