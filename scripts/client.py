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
    python scripts/client.py bid     --tender 招标书.docx --profile-text "..."
    python scripts/client.py compare --old v1.docx --new v2.docx
    python scripts/client.py agent   --file 合同.pdf --goal "全面审查"
"""
from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime
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
    "bid": "tools/check_bid.py",
    "compare": "tools/compare_documents.py",
    "agent": "tools/agent_run.py",
}


# ----------------------------------------------------------------------
# Logging (aligned with local-ai-skill-authoring best practices):
# write to %USERPROFILE%\.openvino\log\docguard-client-py-<ts>.log,
# fall back to <skill>/log/ if the host dir is unavailable.
# Format: [YYYY-MM-DD HH:MM:SS] [<role> pid=<PID>] <message>
# ----------------------------------------------------------------------
def _setup_logging(role: str) -> logging.LoggerAdapter:
    try:
        log_dir = Path.home() / ".openvino" / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        log_dir = ROOT / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_file = log_dir / f"docguard-{role}-{ts}.log"
    logger = logging.getLogger("docguard")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "[%(asctime)s] [%(role)s pid=%(process)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logging.LoggerAdapter(logger, {"role": role})


log = _setup_logging("client-py")


def _usage() -> None:
    print("DocGuard AI 用法: client.py <analyze|search|report|bid|compare|agent> [参数]")
    print("  审查文档 : client.py analyze --file 合同.pdf [--type contract] [--no-llm]")
    print("  知识问答 : client.py search  --query 付款周期 [--doc-id <id>]")
    print("  生成报告 : client.py report  --doc-id <id> --format markdown")
    print("  招标自检 : client.py bid     --tender 招标书.docx --profile-text '资质描述'")
    print("  版本对比 : client.py compare --old v1.docx --new v2.docx")
    print("  自主编排 : client.py agent   --file 合同.pdf --goal '全面审查这份合同'")
    print("  启动服务 : 请使用 scripts/server.py 或 run.ps1 serve")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    # Exit code 1: bad arguments / no action.
    if not argv:
        _usage()
        return 1
    action = argv[0]
    rest = argv[1:]
    if action in TOOLS:
        target = ROOT / TOOLS[action]
        log.info("dispatch action=%s target=%s", action, target)
        # Propagate the tool subprocess exit code (0 success, 1 error, 2 comms).
        return subprocess.call([sys.executable, str(target), *rest])
    log.error("unknown action: %s", action)
    _usage()
    return 1


if __name__ == "__main__":
    sys.exit(main())
