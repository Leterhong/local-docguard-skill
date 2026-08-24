#!/usr/bin/env python3
"""
Tool: check_bid

Agent-facing tool for tender qualification self-check. Extracts the
qualification requirements from a tender document and compares them
against the bidder's own qualifications (free text and/or a local
profile file), returning a go/no-go verdict, per-item match, score,
and blocking gaps.

Usage:
    # tender file + bidder profile text
    python tools/check_bid.py --tender tender.docx --profile-text "注册资本5000万，ISO9001..."
    # tender file + bidder profile file
    python tools/check_bid.py --tender tender.pdf --profile-file our_company.docx
    # reuse a previously analyzed tender (by document_id) + profile text
    python tools/check_bid.py --doc-id <id> --profile-text "..."
    # skip LLM semantic judgement (rule-based only)
    python tools/check_bid.py --tender tender.docx --profile-file p.txt --no-llm

Output:
    JSON bid_evaluation (verdict, score, items, blocking_gaps).
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


def check_bid(
    tender_path: str | None = None,
    document_id: str | None = None,
    profile_text: str = "",
    profile_file: str | None = None,
    use_llm: bool = True,
    use_cloud: bool = False,
    user_id: str = "default",
    auto_start: bool = True,
) -> dict:
    """Run the bid qualification check and return the evaluation."""
    ensure_running(auto_start=auto_start)
    payload = {
        "profile_text": profile_text,
        "use_llm": use_llm,
        "use_cloud": use_cloud,
        "user_id": user_id,
    }
    if document_id:
        payload["document_id"] = document_id
    elif tender_path:
        payload["file_path"] = upload_file(tender_path)
    else:
        raise ValueError("Provide either --tender <file> or --doc-id <id>")

    if profile_file:
        if not Path(profile_file).exists():
            raise FileNotFoundError(f"Profile file not found: {profile_file}")
        payload["profile_file"] = upload_file(profile_file)

    resp = post_json("/api/bid/check", payload, timeout=600)
    if not resp.get("success"):
        raise RuntimeError(f"Bid check failed: {resp.get('error') or resp.get('message')}")
    return resp["data"]


def main():
    parser = argparse.ArgumentParser(
        description="DocGuard AI — tender qualification self-check (go/no-go + gaps)."
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--tender", "-f", help="Path to the tender document")
    src.add_argument("--doc-id", "-d", help="document_id of an already-analyzed tender")
    parser.add_argument("--profile-text", "-p", default="", help="Bidder qualification text")
    parser.add_argument("--profile-file", help="Local file describing the bidder's qualifications")
    parser.add_argument("--no-llm", action="store_true", help="Rule-based matching only, skip LLM")
    parser.add_argument("--cloud", action="store_true", help="Use cloud LLM (requires config, local-only may block)")
    parser.add_argument("--user", default="default")
    parser.add_argument("--no-auto-start", action="store_true")
    args = parser.parse_args()

    if not (args.profile_text.strip() or args.profile_file):
        print("ERROR: provide bidder qualifications via --profile-text and/or --profile-file", file=sys.stderr)
        sys.exit(2)
    if args.profile_file and not Path(args.profile_file).exists():
        print(f"ERROR: profile file not found: {args.profile_file}", file=sys.stderr)
        sys.exit(2)

    result = check_bid(
        tender_path=args.tender,
        document_id=args.doc_id,
        profile_text=args.profile_text,
        profile_file=args.profile_file,
        use_llm=not args.no_llm,
        use_cloud=args.cloud,
        user_id=args.user,
        auto_start=not args.no_auto_start,
    )
    print_json(result)


if __name__ == "__main__":
    main()
