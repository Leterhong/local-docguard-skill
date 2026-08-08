"""
Embedding service.

Loads a local sentence-transformer model (BGE-small-zh-v1.5 by default,
or all-MiniLM-L6-v2 fallback). Models run 100% on localhost.

The service is resilient: if sentence-transformers / torch is not
installed, or the configured model cannot be loaded, it falls back to a
deterministic hashing embedding so the rest of the pipeline (FAISS, RAG)
still works for demos and tests. A warning is logged and `available`
reflects whether a real semantic model is loaded.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional

import numpy as np

from server.config import Settings
from server.services.security import get_logger

logger = get_logger("embedding")


class EmbeddingService:
    def __init__(self, settings: Settings):
        self.settings = settings
        cfg = settings.embedding_cfg
        self.model_name: str = cfg.get("name", "BAAI/bge-small-zh-v1.5")
        self.model_path: str = cfg.get("path", "")
        self.device: str = cfg.get("device", "CPU")
        self.dimension: int = int(cfg.get("dimension", 512))
        self._fallback_name = cfg.get(
            "fallback_name", "sentence-transformers/all-MiniLM-L6-v2"
        )
        self._fallback_dim = int(cfg.get("fallback_dimension", 384))

        self._model = None
        self.available = False
        self.is_fallback = False
        self.loaded_name = ""
        self._load_model()

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------
    def _load_model(self) -> None:
        try:
            # sentence-transformers is the primary backend.
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sentence-transformers not available (%s); using hashing fallback. "
                "Install with: pip install sentence-transformers",
                exc,
            )
            self._activate_fallback()
            return

        # Try local model path first, then HF model name, then fallback model.
        candidates = []
        if self.model_path:
            candidates.append(("local", self.model_path))
        candidates.append(("id", self.model_name))
        candidates.append(("id", self._fallback_name))

        for kind, target in candidates:
            try:
                logger.info("Loading embedding model: %s (%s)", target, kind)
                self._model = SentenceTransformer(target, device=self.device)
                self.available = True
                self.loaded_name = target
                # Update dimension from actual model.
                emb = self._model.encode(["dimension probe"], normalize_embeddings=True)
                self.dimension = int(np.asarray(emb).shape[-1])
                logger.info(
                    "Embedding model loaded: %s (dim=%d, device=%s)",
                    self.loaded_name, self.dimension, self.device,
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to load embedding %s: %s", target, exc)
                continue

        logger.warning("No embedding model could load; using hashing fallback.")
        self._activate_fallback()

    def _activate_fallback(self) -> None:
        self._model = None
        self.available = False
        self.is_fallback = True
        self.dimension = 256
        self.loaded_name = "hashing-fallback"

    # ------------------------------------------------------------------
    # Encoding
    # ------------------------------------------------------------------
    def encode(
        self, texts: List[str], normalize: bool = True, show_progress: bool = False
    ) -> np.ndarray:
        """Encode a list of texts into a (N, dim) float32 matrix."""
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)

        if self._model is not None:
            emb = self._model.encode(
                texts,
                normalize_embeddings=normalize,
                show_progress_bar=show_progress,
                convert_to_numpy=True,
            )
            return np.asarray(emb, dtype=np.float32)

        # Deterministic hashing fallback (bag-of-word-ish hashing).
        vecs = [self._hash_embed(t) for t in texts]
        arr = np.vstack(vecs).astype(np.float32)
        if normalize:
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            arr = arr / norms
        return arr

    def encode_query(self, query: str) -> np.ndarray:
        """Encode a single query; BGE models recommend a query instruction prefix."""
        text = query
        if self.available and "bge" in self.loaded_name.lower():
            text = f"为这个句子生成表示以用于检索相关文章：{query}"
        return self.encode([text])[0]

    def _hash_embed(self, text: str) -> np.ndarray:
        """Deterministic hashing embedding used when no model is available."""
        vec = np.zeros(self.dimension, dtype=np.float32)
        # Tokenize on whitespace and CJK boundaries.
        tokens = []
        buf = ""
        for ch in text:
            if "\u4e00" <= ch <= "\u9fff":
                if buf:
                    tokens.append(buf)
                    buf = ""
                tokens.append(ch)
            elif ch.isalnum():
                buf += ch.lower()
            else:
                if buf:
                    tokens.append(buf)
                    buf = ""
        if buf:
            tokens.append(buf)

        for tok in tokens:
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dimension
            sign = 1.0 if (h >> 8) & 1 else -1.0
            vec[idx] += sign
        return vec
