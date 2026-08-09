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

import sys

# Windows-safe UTF-8 output (mandatory per local-ai-skill-authoring best practices).
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

from server.main import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
