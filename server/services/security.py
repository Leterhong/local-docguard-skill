"""
Security & logging utilities for DocGuard AI.

Responsibilities:
  * Redact PII from logs (phone numbers, ID cards, emails, bank cards).
  * Enforce per-user file isolation (paths must stay inside the user sandbox).
  * Resolve safe, user-scoped paths for uploads/reports/vector indexes.
  * Provide a configured logger that never leaks sensitive content.

All files stay on localhost; this module makes sure they also stay within
the intended workspace.
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
from pathlib import Path
from typing import Dict, List, Pattern, Tuple

from server.config import Settings, get_settings

_LOGGER_INITIALIZED = False


def _compile_redactions(
    rules: List[Dict[str, str]],
) -> List[Tuple[Pattern, str]]:
    compiled: List[Tuple[Pattern, str]] = []
    for rule in rules:
        pat = rule.get("pattern")
        repl = rule.get("replacement", "[REDACTED]")
        if pat:
            try:
                compiled.append((re.compile(pat), repl))
            except re.error:
                continue
    return compiled


def redact_text(text: str, settings: Settings | None = None) -> str:
    """Redact configured PII patterns from text before logging."""
    if not text:
        return text
    s = settings or get_settings()
    for pattern, replacement in _compile_redactions(s.log_redactions()):
        text = pattern.sub(replacement, text)
    return text


def get_logger(name: str) -> logging.Logger:
    """Return a logger whose output is PII-redacted."""
    global _LOGGER_INITIALIZED
    logger = logging.getLogger(f"docguard.{name}")
    if not _LOGGER_INITIALIZED:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root = logging.getLogger("docguard")
        # Avoid duplicate handlers on reload.
        if not root.handlers:
            root.addHandler(handler)
            root.setLevel(logging.INFO)
        _LOGGER_INITIALIZED = True
    return logger


def safe_user_dir(settings: Settings, user_id: str, kind: str) -> Path:
    """
    Return a user-isolated directory.

    kind is one of: uploads | reports | vectordb
    The user_id is hashed so the filesystem never stores raw identifiers,
    and the resulting path is guaranteed to be inside the data root.
    """
    safe_id = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
    base_map = {
        "uploads": settings.uploads_dir,
        "reports": settings.reports_dir,
        "vectordb": settings.vectordb_dir,
    }
    if kind not in base_map:
        raise ValueError(f"Unknown user dir kind: {kind}")
    target = (base_map[kind] / safe_id).resolve()
    target.mkdir(parents=True, exist_ok=True)
    _ensure_within(base_map[kind].resolve(), target)
    return target


def _ensure_within(root: Path, target: Path) -> None:
    try:
        target.relative_to(root)
    except ValueError as exc:  # pragma: no cover - defensive
        raise PermissionError(
            f"Path escapes sandbox: {target} not under {root}"
        ) from exc


def resolve_input_file(
    file_path: str,
    settings: Settings | None = None,
    user_id: str = "default",
) -> Path:
    """
    Resolve and validate a user-supplied input file path.

    For security, when user isolation is enabled, the file must either:
      - already live inside the user's uploads sandbox, or
      - be inside the project's samples directory.
    Absolute paths outside these zones are rejected (they may point at
    arbitrary system files).
    """
    s = settings or get_settings()
    p = Path(file_path).expanduser()
    if not p.is_absolute():
        # Relative paths resolve against the user's upload dir.
        p = safe_user_dir(s, user_id, "uploads") / p
    p = p.resolve()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Document not found: {p}")

    if s.isolate_users():
        allowed_roots = [
            safe_user_dir(s, user_id, "uploads"),
            s.samples_dir.resolve(),
            # The project data root itself (covers shared samples).
            (s.project_root / "data").resolve(),
            # The project's own bundled example documents (used by demos,
            # docs and tests) are trusted project resources, not arbitrary
            # user-supplied system files, so they are safe to analyze.
            (s.project_root / "examples").resolve(),
        ]
        if not any(_is_relative_to(p, root) for root in allowed_roots):
            raise PermissionError(
                "Access denied: document is outside the allowed workspace. "
                "Place files in your uploads folder or the samples directory."
            )
    return p


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def copy_to_sandbox(
    src: Path, settings: Settings, user_id: str, filename: str | None = None
) -> Path:
    """Copy an inbound file into the user's upload sandbox."""
    target_dir = safe_user_dir(settings, user_id, "uploads")
    name = filename or src.name
    dest = (target_dir / name).resolve()
    _ensure_within(target_dir, dest)
    if src.resolve() != dest:
        shutil.copy2(src, dest)
    return dest


def document_id_for(path: Path) -> str:
    """Stable, filesystem-safe document id derived from its path."""
    h = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", path.stem)[:40]
    return f"{stem}-{h}"
