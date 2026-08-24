"""
DocGuard AI - Agent orchestrator tests (deterministic planner, no model).

Run with a working environment:
    python -m pytest tests/test_agent.py -q

These tests exercise POST /api/agent/run with use_llm=False so the
deterministic planner drives the multi-step tool chain (analyze ->
bid check / compare / RAG search) without any local model. The full
step trace is asserted so the agentic chain stays observable.
"""
from __future__ import annotations
import os
import shutil
import sys

# Windows-safe UTF-8 output (mandatory per local-ai-skill-authoring best practices).
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402

client = TestClient(app)

# resolve_input_file enforces the user sandbox: only uploads/, samples/
# and data/ are readable. Copy fixtures into the allowed data zone first.
SAMPLES = os.path.join(ROOT, "data", "samples")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")


def _fixture(name: str) -> str:
    """Copy a fixture into the allowed data/ zone and return its ABSOLUTE
    path (resolve_input_file treats relative paths as upload-dir-relative,
    which would 404)."""
    os.makedirs(SAMPLES, exist_ok=True)
    dst = os.path.abspath(os.path.join(SAMPLES, name))
    if not os.path.exists(dst):
        shutil.copy(os.path.join(FIXTURES, name), dst)
    return dst

PROFILE_TEXT = (
    "我公司注册资本1000万元，具有ISO9001质量管理体系认证，是高新技术企业，"
    "近三年完成过3个类似智慧校园项目，拥有2名信息系统项目管理师。"
)


def test_agent_run_tender_bid_chain():
    """Tender + profile -> planner must chain analyze then bid self-check."""
    tender = _fixture("sample_tender.txt")
    r = client.post(
        "/api/agent/run",
        json={
            "goal": "审查这份招标文件的风险，并判断我方是否具备投标资格",
            "file_paths": [tender],
            "profile_text": PROFILE_TEXT,
            "use_llm": False,
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["planner"] == "deterministic"
    actions = [s["action"] for s in data["steps"]]
    assert actions[0] == "analyze_document", actions
    assert "check_bid_qualification" in actions, actions
    bid = data["artifacts"]["check_bid_qualification"]
    assert bid["score"] > 0
    assert "verdict" in bid


def test_agent_run_compare_chain():
    """Two contract versions -> planner must analyze both and compare."""
    v1 = _fixture("contract_v1.txt")
    v2 = _fixture("contract_v2.txt")
    r = client.post(
        "/api/agent/run",
        json={
            "goal": "对比这两版合同的重大变化",
            "file_paths": [v1, v2],
            "use_llm": False,
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    actions = [s["action"] for s in data["steps"]]
    assert actions.count("analyze_document") == 2, actions
    assert "compare_versions" in actions, actions
    cmp = data["artifacts"]["compare_versions"]
    assert cmp["change_count"] >= 1


def test_agent_run_question_triggers_search():
    """An explicit question must add a RAG search step against the doc."""
    v1 = _fixture("contract_v1.txt")
    r = client.post(
        "/api/agent/run",
        json={
            "goal": "审查合同并回答付款约定",
            "file_paths": [v1],
            "question": "付款方式是什么",
            "use_llm": False,
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    actions = [s["action"] for s in data["steps"]]
    assert "search_knowledge" in actions, actions
    sch = data["artifacts"]["search_knowledge"]
    assert len(sch["chunks"]) > 0


def test_agent_run_validates_input():
    r = client.post("/api/agent/run", json={"goal": ""})
    assert r.status_code == 422
    r = client.post("/api/agent/run", json={"goal": "做事"})
    assert r.status_code == 422


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
