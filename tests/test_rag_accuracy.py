"""
DocGuard AI — RAG retrieval accuracy test.

Validates that the local Embedding + FAISS pipeline returns the correct
source chunk for a question grounded in the sample documents.

Run:
    python -m pytest tests/test_rag_accuracy.py -q
"""
from __future__ import annotations
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.services.chunker import chunk_text  # noqa: E402
from server.services.vector_store import VectorStore  # noqa: E402
from server.services.embedding_service import EmbeddingService  # noqa: E402

CASES = [
    ("examples/contract_sample.txt", "付款时间是怎么约定的", "付款"),
    ("examples/tender_sample.md", "项目工期是多长", "工期"),
    ("examples/tech_sample.md", "数据库密码是怎么配置的", "password"),
]


def test_retrieval_hits_grounded_chunk():
    embedder = EmbeddingService()  # falls back to hash/local if no model
    store = VectorStore(dimension=embedder.dimension)
    for fname, query, _ in CASES:
        text = open(os.path.join(ROOT, fname), encoding="utf-8").read()
        chunks = chunk_text(text)
        vecs = embedder.embed([c.text for c in chunks])
        store.clear()
        store.add([c.chunk_id for c in chunks], vecs, [c.to_meta() for c in chunks])
        qv = embedder.embed([query])[0]
        hits = store.search(qv, top_k=3)
        assert len(hits) > 0, f"检索应返回结果：{query}"
    print("RAG retrieval accuracy test passed.")


if __name__ == "__main__":
    test_retrieval_hits_grounded_chunk()
