#!/usr/bin/env python3
"""
client.py — short-lived DocGuard CLI entry point (contract entry).

This file satisfies the local-ai-skill-authoring required filename
`scripts/client.py` (the short-lived client). It routes user actions to the
Agent tools under `tools/`, which talk to the local FastAPI server over HTTP
and auto-start it when needed.

Usage (also reachable via `scripts/run.ps1 <action>`):
    python scripts/client.py analyze --file 合同.pdf [--type contract]
    python scripts/client.py search  --query 付款周期 [--doc-id <id>]
    python scripts/client.py report  --doc-id <id> --format markdown
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Windows-safe UTF-8 output (mandatory per local-ai-skill-authoring best practices).
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
TOOLS = {
    "analyze": "tools/analyze_document.py",
    "search": "tools/search_document.py",
    "report": "tools/generate_report.py",
}


def _usage() -> None:
    print("DocGuard AI 用法: client.py <analyze|search|report> [参数]")
    print("  审查文档 : client.py analyze --file 合同.pdf [--type contract] [--no-llm]")
    print("  知识问答 : client.py search  --query 付款周期 [--doc-id <id>]")
    print("  生成报告 : client.py report  --doc-id <id> --format markdown")
    print("  启动服务 : 请使用 scripts/server.py 或 run.ps1 serve")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv:
        _usage()
        return 0
    action = argv[0]
    rest = argv[1:]
    if action in TOOLS:
        target = ROOT / TOOLS[action]
        return subprocess.call([sys.executable, str(target), *rest])
    _usage()
    return 0


if __name__ == "__main__":
    sys.exit(main())
