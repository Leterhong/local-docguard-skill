"""
Service container — assembles and holds all service singletons.

The FastAPI app and the Agent tools both obtain their service references
through this container. This keeps construction in one place and makes
the system testable (tests can build a container with temp settings).
"""
from __future__ import annotations

import threading
from typing import Optional

from server.config import Settings, get_settings
from server.services.analysis_engine import AnalysisEngine
from server.services.embedding_service import EmbeddingService
from server.services.llm_service import LLMService
from server.services.ocr_service import OcrService
from server.services.security import get_logger
from server.services.vector_store import VectorStore

logger = get_logger("container")


class ServiceContainer:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        # RLock: lazy properties can nest (e.g. `engine` takes the lock and
        # then accesses `self.ocr`, which takes it again). A plain Lock
        # deadlocks on that same-thread re-entry; RLock allows it.
        self._lock = threading.RLock()
        self._ocr: Optional[OcrService] = None
        self._embedder: Optional[EmbeddingService] = None
        self._llm: Optional[LLMService] = None
        self._vector_store: Optional[VectorStore] = None
        self._engine: Optional[AnalysisEngine] = None

    # ------------------------------------------------------------------
    # Lazy-loaded singletons
    # ------------------------------------------------------------------
    @property
    def ocr(self) -> OcrService:
        if self._ocr is None:
            with self._lock:
                if self._ocr is None:
                    self._ocr = OcrService(self.settings)
        return self._ocr

    @property
    def embedder(self) -> EmbeddingService:
        if self._embedder is None:
            with self._lock:
                if self._embedder is None:
                    self._embedder = EmbeddingService(self.settings)
        return self._embedder

    @property
    def llm(self) -> LLMService:
        if self._llm is None:
            with self._lock:
                if self._llm is None:
                    self._llm = LLMService(self.settings)
        return self._llm

    @property
    def vector_store(self) -> VectorStore:
        if self._vector_store is None:
            with self._lock:
                if self._vector_store is None:
                    # Sync dimension with the loaded embedder.
                    emb = self.embedder
                    store = VectorStore(self.settings)
                    store.dim = emb.dimension
                    self._vector_store = store
        return self._vector_store

    @property
    def engine(self) -> AnalysisEngine:
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    self._engine = AnalysisEngine(
                        settings=self.settings,
                        ocr=self.ocr,
                        embedder=self.embedder,
                        llm=self.llm,
                        vector_store=self.vector_store,
                    )
        return self._engine

    # ------------------------------------------------------------------
    # Status / health
    # ------------------------------------------------------------------
    def health(self) -> dict:
        llm_info = self.llm.info()
        return {
            "model_loaded": llm_info["available"],
            "model_name": llm_info["loaded_name"] or self.llm.name,
            "model_device": llm_info["device"],
            "embedding_loaded": self.embedder.available,
            "embedding_model": self.embedder.loaded_name,
            "ocr_available": self.ocr.available,
            "documents_indexed": self.vector_store.document_count(),
            "chunks_indexed": self.vector_store.chunk_count(),
            "provider": llm_info["provider"],
            "cloud_available": llm_info["cloud_available"],
            "cloud_enabled": llm_info["cloud_enabled"],
            "local_only": llm_info["local_only"],
        }


# Module-level singleton used by the API and tools.
_container: Optional[ServiceContainer] = None
_container_lock = threading.Lock()


def get_container() -> ServiceContainer:
    global _container
    if _container is None:
        with _container_lock:
            if _container is None:
                _container = ServiceContainer()
    return _container


def reset_container(settings: Optional[Settings] = None) -> ServiceContainer:
    """Reset the singleton (used by tests)."""
    global _container
    with _container_lock:
        _container = ServiceContainer(settings) if settings else ServiceContainer()
    return _container
