#!/usr/bin/env python3
"""
Tool: generate_report

Agent-facing tool that turns an analysis result into a downloadable
report (Markdown / HTML / JSON). Can consume either:
  * a document_id (uses the cached analysis from the last analyze call), or
  * a JSON file containing the full analysis_result.

Usage:
    python tools/generate_report.py --doc-id <id> --format html
    python tools/generate_report.py --analysis result.json --format markdown

Output:
    JSON with report_id, file_path, download_url.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Windows-safe UTF-8 output (mandatory per local-ai-skill-authoring best practices).
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _client import BASE_URL, ensure_running, post_json, print_json  # noqa: E402


def generate_report(
    analysis_result: dict,
    fmt: str = "markdown",
    user_id: str = "default",
    auto_start: bool = True,
) -> dict:
    ensure_running(auto_start=auto_start)
    payload = {
        "analysis_result": analysis_result,
        "format": fmt,
        "user_id": user_id,
    }
    resp = post_json("/api/report", payload, timeout=120)
    if not resp.get("success"):
        raise RuntimeError(f"Report generation failed: {resp.get('error') or resp.get('message')}")
    data = resp["data"]
    if data.get("download_url"):
        data["download_url"] = BASE_URL + data["download_url"]
    return data


def main():
    parser = argparse.ArgumentParser(description="DocGuard AI — generate a review report.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--doc-id", help="Use a cached analysis by document id")
    group.add_argument("--analysis", help="Path to a JSON file containing analysis_result")
    parser.add_argument(
        "--format", "-f",
        choices=["markdown", "html", "json"],
        default="markdown",
    )
    parser.add_argument("--user", default="default")
    parser.add_argument("--no-auto-start", action="store_true")
    args = parser.parse_args()

    if args.doc_id:
        analysis_result = {"document_id": args.doc_id}
    else:
        path = Path(args.analysis)
        if not path.exists():
            print(f"ERROR: analysis file not found: {path}", file=sys.stderr)
            sys.exit(2)
        analysis_result = json.loads(path.read_text(encoding="utf-8"))

    result = generate_report(
        analysis_result=analysis_result,
        fmt=args.format,
        user_id=args.user,
        auto_start=not args.no_auto_start,
    )
    print_json(result)


if __name__ == "__main__":
    main()
