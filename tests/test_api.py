"""
DocGuard AI — API integration tests (FastAPI TestClient).

Run with a working environment:
    pip install fastapi uvicorn pydantic numpy faiss-cpu
    python -m pytest tests/test_api.py -q

The /api/analyze endpoint exercises the full pipeline (parse -> chunk ->
embed -> FAISS -> rules -> optionally LLM). With no LLM loaded it falls
back to rule-engine mode and still returns real findings.
"""
from __future__ import annotations
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


def test_analyze_sample():
    path = os.path.join(ROOT, "examples", "contract_sample.txt")
    r = client.post("/api/analyze", json={"file_path": path, "use_llm": False})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["file_name"] == "contract_sample.txt"
    assert len(data["risks"]) > 0, "分析结果应含风险项"
    assert data["overall_risk_level"] in ("Low", "Medium", "High")


def test_search_rag():
    # Index the contract first, then ask a question.
    path = os.path.join(ROOT, "examples", "contract_sample.txt")
    a = client.post("/api/analyze", json={"file_path": path, "use_llm": False})
    doc_id = a.json()["data"]["document_id"]
    r = client.post("/api/search", json={"query": "付款周期是多久", "document_id": doc_id, "top_k": 3})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert len(data["chunks"]) > 0


if __name__ == "__main__":
    test_health()
    test_analyze_sample()
    test_search_rag()
    print("API tests passed.")
