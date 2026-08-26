"""
Central configuration loader for DocGuard AI.

Loads model_config.yaml once and exposes a typed Settings object to all
services. Paths are resolved relative to the project root so the server
can be started from any working directory.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger("docguard.config")

# Project root = parent of the server/ directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "model_config.yaml"


class Settings:
    """Read-only view of the merged configuration."""

    def __init__(self, data: Dict[str, Any], config_path: Path):
        self._data = data
        self._config_path = config_path
        self.project_root = PROJECT_ROOT
        # Resolve runtime data directories under project root.
        self.uploads_dir = self._resolve_path(
            data.get("vectordb", {}).get("path", "data/vectordb")
        ).parent / "uploads"
        self.reports_dir = self._resolve_path(
            data.get("vectordb", {}).get("path", "data/vectordb")
        ).parent / "reports"
        self.vectordb_dir = self._resolve_path(
            data.get("vectordb", {}).get("path", "data/vectordb")
        )
        self.samples_dir = PROJECT_ROOT / "data" / "samples"
        for d in (self.uploads_dir, self.reports_dir, self.vectordb_dir, self.samples_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------
    @property
    def raw_config(self) -> Dict[str, Any]:
        """Expose the full merged configuration dict."""
        return self._data

    @property
    def model(self) -> Dict[str, Any]:
        return self._data.get("model", {})

    @property
    def embedding_cfg(self) -> Dict[str, Any]:
        return self._data.get("embedding", {})

    @property
    def ocr_cfg(self) -> Dict[str, Any]:
        return self._data.get("ocr", {})

    @property
    def vectordb_cfg(self) -> Dict[str, Any]:
        return self._data.get("vectordb", {})

    @property
    def server_cfg(self) -> Dict[str, Any]:
        return self._data.get("server", {})

    @property
    def security(self) -> Dict[str, Any]:
        return self._data.get("security", {})

    @property
    def processing(self) -> Dict[str, Any]:
        return self._data.get("processing", {})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _resolve_path(self, value: str) -> Path:
        p = Path(value)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p.resolve()

    def resolve_model_path(self, path: str) -> Path:
        return self._resolve_path(path)

    def host(self) -> str:
        return self.server_cfg.get("host", "127.0.0.1")

    def port(self) -> int:
        # 环境变量优先，与客户端 DOCGUARD_PORT 保持一致，避免双源不一致
        env_port = os.environ.get("DOCGUARD_PORT", "").strip()
        if env_port:
            try:
                return int(env_port)
            except ValueError:
                logger.warning("Invalid DOCGUARD_PORT=%r; falling back to config.", env_port)
        return int(self.server_cfg.get("port", 8765))

    def cors_origins(self) -> List[str]:
        return self.server_cfg.get("cors_origins", ["http://localhost:8765"])

    def is_local_only(self) -> bool:
        return bool(self.security.get("local_only", True))

    def log_redactions(self) -> List[Dict[str, str]]:
        return self.security.get("log_redactions", [])

    def isolate_users(self) -> bool:
        return bool(self.security.get("isolate_users", True))


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=1)
def get_settings(config_path: Optional[str] = None) -> Settings:
    """Return a cached Settings instance.

    The config path can be overridden with the DOCGUARD_CONFIG environment
    variable (useful for tests).
    """
    cfg_path = Path(
        config_path
        or os.environ.get("DOCGUARD_CONFIG")
        or DEFAULT_CONFIG_PATH
    ).resolve()
    data = _load_yaml(cfg_path)
    return Settings(data, cfg_path)


def reload_settings(config_path: Optional[str] = None) -> Settings:
    """Force-reload configuration (used by tests)."""
    get_settings.cache_clear()
    return get_settings(config_path)
