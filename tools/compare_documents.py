#!/usr/bin/env python3
"""
Tool: compare_documents

Agent-facing tool that diffs two versions of a document (contract,
tender, technical proposal...) and produces a structured comparison:
added / removed / modified segments, plus a plain-language summary
flagging high-risk changes (amounts, dates, liability, parties).

Usage:
    python tools/compare_documents.py --old contract_v1.docx --new contract_v2.docx
    python tools/compare_documents.py -a old.pdf -b new.pdf --type contract

Output:
    JSON comparison (segments, change_count, summary).
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


def compare_documents(
    file_a: str,
    file_b: str,
    doc_type: str | None = None,
    auto_start: bool = True,
) -> dict:
    """Compare two document versions and return the structured diff."""
    ensure_running(auto_start=auto_start)
    payload = {
        "file_path_a": upload_file(file_a),
        "file_path_b": upload_file(file_b),
    }
    if doc_type:
        payload["doc_type_hint"] = doc_type
    resp = post_json("/api/compare", payload, timeout=300)
    if not resp.get("success"):
        raise RuntimeError(f"Compare failed: {resp.get('error') or resp.get('message')}")
    return resp["data"]


def main():
    parser = argparse.ArgumentParser(
        description="DocGuard AI — diff two document versions and flag risky changes."
    )
    parser.add_argument("--old", "-a", required=True, help="Path to the old/base document")
    parser.add_argument("--new", "-b", required=True, help="Path to the new/revised document")
    parser.add_argument(
        "--type", "-t",
        choices=["contract", "tender", "technical", "prd", "policy", "general"],
        help="Document type hint",
    )
    parser.add_argument("--no-auto-start", action="store_true")
    args = parser.parse_args()

    for label, p in (("--old", args.old), ("--new", args.new)):
        if not Path(p).exists():
            print(f"ERROR: {label} file not found: {p}", file=sys.stderr)
            sys.exit(2)

    result = compare_documents(
        file_a=args.old,
        file_b=args.new,
        doc_type=args.type,
        auto_start=not args.no_auto_start,
    )
    print_json(result)


if __name__ == "__main__":
    main()
