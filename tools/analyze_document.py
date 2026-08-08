#!/usr/bin/env python3
"""
Tool: analyze_document

Agent-facing tool for full document analysis. Invoked by Qoder / WorkBuddy
/ TRAE Work through the Skill's SKILL.md tool definition.

Usage:
    python tools/analyze_document.py --file "C:/path/to/contract.pdf"
    python tools/analyze_document.py --file contract.docx --type tender
    python tools/analyze_document.py --file contract.pdf --no-llm

Output:
    JSON document_analysis (see server/models/schemas.py:DocumentAnalysis).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running directly: python tools/analyze_document.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _client import ensure_running, post_json, print_json, upload_file  # noqa: E402


def analyze_document(
    file_path: str,
    doc_type: str | None = None,
    use_llm: bool = True,
    use_cloud: bool = False,
    user_id: str = "default",
    auto_start: bool = True,
) -> dict:
    """Run analysis on a document and return the structured result.

    The local file is first uploaded into the server's isolated uploads
    folder (so the server's workspace sandbox accepts it), then analyzed
    via the returned server-side path. This lets the Agent point the tool
    at any local document without relaxing the security boundary.
    """
    ensure_running(auto_start=auto_start)
    server_path = upload_file(file_path)
    payload = {
        "file_path": server_path,
        "use_llm": use_llm,
        "use_cloud": use_cloud,
        "user_id": user_id,
    }
    if doc_type:
        payload["doc_type_hint"] = doc_type
    resp = post_json("/api/analyze", payload, timeout=600)
    if not resp.get("success"):
        raise RuntimeError(f"Analysis failed: {resp.get('error') or resp.get('message')}")
    return resp["data"]


def main():
    parser = argparse.ArgumentParser(
        description="DocGuard AI — analyze an enterprise document (contract / tender / technical)."
    )
    parser.add_argument("--file", "-f", required=True, help="Absolute path to the document")
    parser.add_argument(
        "--type", "-t",
        choices=["contract", "tender", "technical", "prd", "policy", "general"],
        help="Document type hint (auto-detected if omitted)",
    )
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM, use rule engine only")
    parser.add_argument("--cloud", action="store_true", help="Use cloud LLM instead of local model (requires config)")
    parser.add_argument("--user", default="default", help="User id for file isolation")
    parser.add_argument("--no-auto-start", action="store_true", help="Do not auto-start the server")
    args = parser.parse_args()

    if not Path(args.file).exists():
        print(f"ERROR: file not found: {args.file}", file=sys.stderr)
        sys.exit(2)

    result = analyze_document(
        file_path=args.file,
        doc_type=args.type,
        use_llm=not args.no_llm,
        use_cloud=args.cloud,
        user_id=args.user,
        auto_start=not args.no_auto_start,
    )
    print_json(result)


if __name__ == "__main__":
    main()
