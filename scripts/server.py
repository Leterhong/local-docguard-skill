#!/usr/bin/env python3
"""
server.py — long-lived DocGuard model / inference server (contract entry).

This file satisfies the local-ai-skill-authoring required filename
`scripts/server.py` (the long-lived model service). DocGuard uses an HTTP
transport (FastAPI on 127.0.0.1:8765) instead of the named-pipe reference
implementation; the spec explicitly permits HTTP as an alternative transport.

Usage (also reachable via `scripts/run.ps1 serve`):
    python scripts/server.py
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

# Windows-safe UTF-8 output (mandatory per local-ai-skill-authoring best practices).
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")


# ----------------------------------------------------------------------
# Logging (aligned with local-ai-skill-authoring best practices):
# write to %USERPROFILE%\.openvino\log\docguard-server-py-<ts>.log,
# fall back to <skill>/log/ if the host dir is unavailable.
# Format: [YYYY-MM-DD HH:MM:SS] [<role> pid=<PID>] <message>
# ----------------------------------------------------------------------
def _setup_logging(role: str) -> logging.LoggerAdapter:
    try:
        log_dir = Path.home() / ".openvino" / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        log_dir = Path(__file__).resolve().parent.parent / "log"
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


log = _setup_logging("server-py")


def main() -> int:
    from server.main import main as _run  # noqa: E402

    return _run()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.info("interrupted by user, exiting")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        log.exception("server crashed: %s", exc)
        sys.exit(1)
