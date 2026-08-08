"""
Vector store backed by FAISS (local, in-process).

Stores document chunks with metadata and supports:
  * add / upsert per document
  * cosine similarity search
  * per-document listing & deletion
  * persistence to disk (index + JSON metadata)

FAISS is optional. If faiss-cpu is not installed, the store falls back to
a brute-force numpy implementation so RAG still functions.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from server.config import Settings
from server.services.security import get_logger

logger = get_logger("vectorstore")


@dataclass
class ChunkRecord:
    chunk_id: str
    document_id: str
    file_name: str
    text: str
    page: Optional[int] = None
    section: str = ""
    metadata: Dict = field(default_factory=dict)


class VectorStore:
    def __init__(self, settings: Settings, namespace: str = "default"):
        self.settings = settings
        self.dim: int = int(settings.embedding_cfg.get("dimension", 512))
        self.namespace = namespace
        self.store_dir = settings.vectordb_dir / namespace
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.store_dir / "faiss.index"
        self.meta_path = self.store_dir / "chunks.json"

        self._lock = threading.RLock()
        self._records: List[ChunkRecord] = []
        self._vectors: Optional[np.ndarray] = None  # (N, dim) float32, L2-normalized
        self._index = None
        self._faiss_available = False
        self._init_backend()
        self._load()

    # ------------------------------------------------------------------
    # Backend
    # ------------------------------------------------------------------
    def _init_backend(self) -> None:
        try:
            import faiss  # type: ignore

            self._faiss = faiss
            self._faiss_available = True
            logger.info("FAISS backend available (version %s).", faiss.__version__)
        except Exception as exc:  # noqa: BLE001
            self._faiss = None
            self._faiss_available = False
            logger.warning(
                "FAISS not available (%s); using numpy brute-force fallback.", exc
            )

    def _load(self) -> None:
        if self.meta_path.exists():
            try:
                raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
                self._records = [ChunkRecord(**r) for r in raw]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not load chunk metadata: %s", exc)
                self._records = []

        if self._records and self.index_path.exists() and self._faiss_available:
            try:
                self._index = self._faiss.read_index(str(self.index_path))
                self.dim = self._index.d
                if self._index.ntotal == len(self._records):
                    # Reconstruct in-memory vectors so records and vectors
                    # stay perfectly aligned for numpy scoring.
                    self._vectors = self._faiss.reconstruct_n(
                        0, self._index.ntotal
                    ).astype(np.float32)
                else:
                    # Inconsistent on-disk state: drop to a clean empty store
                    # rather than serving a desynced (records != vectors) index.
                    logger.warning(
                        "FAISS index count %d != records %d; resetting store.",
                        self._index.ntotal, len(self._records),
                    )
                    self._records = []
                    self._vectors = None
                    self._index = None
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not load FAISS index: %s", exc)
                self._index = None
                self._vectors = None
        else:
            self._rebuild_vectors()

    def _rebuild_vectors(self) -> None:
        if not self._records:
            self._vectors = np.zeros((0, self.dim), dtype=np.float32)
            self._index = None
            return
        # Vectors must be supplied via add(); if missing, re-encode externally.
        # Here we keep whatever vectors were attached; add() handles persistence.
        if self._vectors is None:
            self._vectors = np.zeros((len(self._records), self.dim), dtype=np.float32)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def add(
        self,
        records: List[ChunkRecord],
        vectors: np.ndarray,
        persist: bool = True,
    ) -> int:
        if vectors.shape[0] != len(records):
            raise ValueError("records and vectors length mismatch")
        if vectors.size and vectors.shape[1] != self.dim:
            # Adapt dimension if embedding model changed.
            self.dim = vectors.shape[1]

        with self._lock:
            # Remove any existing chunks for these documents first.
            doc_ids = {r.document_id for r in records}
            keep = [
                (i, r)
                for i, r in enumerate(self._records)
                if r.document_id not in doc_ids
            ]
            if self._records:
                kept_idx = [i for i, _ in keep]
                self._records = [r for _, r in keep]
                if self._vectors is not None and len(kept_idx) == self._vectors.shape[0]:
                    self._vectors = self._vectors[kept_idx]
                else:
                    self._vectors = None

            self._records.extend(records)
            new_vecs = vectors.astype(np.float32, copy=False)
            # Normalize for cosine (inner product on normalized vectors).
            new_vecs = self._normalize(new_vecs)
            if self._vectors is None or self._vectors.size == 0:
                self._vectors = new_vecs
            else:
                if self._vectors.shape[1] != new_vecs.shape[1]:
                    # Dimension mismatch; reset and keep only new vectors.
                    self._records = list(records)
                    self._vectors = new_vecs
                else:
                    self._vectors = np.vstack([self._vectors, new_vecs])

            self._build_faiss_index()
            if persist:
                self._persist()
            return len(records)

    def _build_faiss_index(self) -> None:
        if not self._faiss_available or self._vectors is None or self._vectors.size == 0:
            self._index = None
            return
        dim = self._vectors.shape[1]
        index = self._faiss.IndexFlatIP(dim)
        index.add(self._vectors)
        self._index = index

    def remove_document(self, document_id: str, persist: bool = True) -> int:
        with self._lock:
            keep = [
                (i, r)
                for i, r in enumerate(self._records)
                if r.document_id != document_id
            ]
            removed = len(self._records) - len(keep)
            if removed == 0:
                return 0
            self._records = [r for _, r in keep]
            if self._vectors is not None and removed:
                keep_idx = [i for i, _ in keep]
                if len(keep_idx) == self._vectors.shape[0]:
                    pass
                else:
                    self._vectors = self._vectors[keep_idx] if keep_idx else np.zeros(
                        (0, self.dim), dtype=np.float32
                    )
            self._build_faiss_index()
            if persist:
                self._persist()
            return removed

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 6,
        document_id: Optional[str] = None,
    ) -> List[tuple]:
        """Return list of (ChunkRecord, score) sorted desc."""
        with self._lock:
            n_vec = 0 if self._vectors is None else self._vectors.shape[0]
            if not self._records or n_vec == 0:
                return []
            # Guard against any residual records/vectors desync.
            n = min(len(self._records), n_vec)
            q = self._normalize(query_vector.reshape(1, -1).astype(np.float32))[0]
            scores = self._vectors[:n] @ q
            # Optionally restrict to one document.
            if document_id:
                mask = np.array(
                    [r.document_id == document_id for r in self._records[:n]],
                    dtype=bool,
                )
                scores = np.where(mask, scores, -1.0)

            k = min(top_k, n)
            if k <= 0:
                return []
            kth = min(k - 1, n - 1)
            top_idx = np.argpartition(-scores, kth)[:k]
            top_idx = sorted(top_idx, key=lambda i: -scores[i])
            return [(self._records[i], float(scores[i])) for i in top_idx]

    def list_documents(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.document_id] = counts.get(r.document_id, 0) + 1
        return counts

    def document_count(self) -> int:
        return len(self.list_documents())

    def chunk_count(self) -> int:
        return len(self._records)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _persist(self) -> None:
        try:
            self.meta_path.write_text(
                json.dumps([asdict(r) for r in self._records], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if self._faiss_available and self._index is not None:
                self._faiss.write_index(self._index, str(self.index_path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to persist vector store: %s", exc)

    @staticmethod
    def _normalize(vecs: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms
