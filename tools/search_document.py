#!/usr/bin/env python3
"""
Tool: search_document

Agent-facing tool for RAG-based document Q&A. Retrieves the most relevant
chunks from previously analyzed documents and (when the local LLM is
available) generates a grounded answer with cited sources.

Usage:
    python tools/search_document.py --query "这个合同的付款周期是多少？"
    python tools/search_document.py --query "违约条款" --doc-id <id> --top-k 5

Output:
    JSON retrieved_context (query, answer, chunks with scores & locations).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _client import ensure_running, post_json, print_json  # noqa: E402


def search_document(
    query: str,
    document_id: str | None = None,
    top_k: int = 6,
    use_cloud: bool = False,
    user_id: str = "default",
    auto_start: bool = True,
) -> dict:
    ensure_running(auto_start=auto_start)
    payload = {
        "query": query,
        "top_k": top_k,
        "use_cloud": use_cloud,
        "user_id": user_id,
    }
    if document_id:
        payload["document_id"] = document_id
    resp = post_json("/api/search", payload, timeout=120)
    if not resp.get("success"):
        raise RuntimeError(f"Search failed: {resp.get('error') or resp.get('message')}")
    return resp["data"]


def main():
    parser = argparse.ArgumentParser(description="DocGuard AI — RAG search over analyzed documents.")
    parser.add_argument("--query", "-q", required=True, help="Natural language question")
    parser.add_argument("--doc-id", "-d", help="Restrict search to a specific document id")
    parser.add_argument("--top-k", "-k", type=int, default=6, help="Number of chunks to retrieve")
    parser.add_argument("--cloud", action="store_true", help="Use cloud LLM for answer generation (requires config)")
    parser.add_argument("--user", default="default")
    parser.add_argument("--no-auto-start", action="store_true")
    args = parser.parse_args()

    result = search_document(
        query=args.query,
        document_id=args.doc_id,
        top_k=args.top_k,
        use_cloud=args.cloud,
        user_id=args.user,
        auto_start=not args.no_auto_start,
    )
    print_json(result)


if __name__ == "__main__":
    main()
