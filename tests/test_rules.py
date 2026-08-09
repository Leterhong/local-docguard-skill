"""
DocGuard AI — rule engine tests.

These tests run WITHOUT any external model or service: they load the
sample documents in examples/ and assert that the deterministic rule
engine produces real, evidence-backed findings. This is the safety net
that guarantees the Skill never returns empty/fake output.

Run:
    python -m pytest tests/test_rules.py -q
    # or:  python tests/test_rules.py
"""
from __future__ import annotations
import os
import sys

# Windows-safe UTF-8 output (mandatory per local-ai-skill-authoring best practices).
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

# Make the project root importable (so `import server...` works).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.services.rules_engine import (  # noqa: E402
    ContractRuleEngine,
    TenderRuleEngine,
    TechnicalRuleEngine,
)

EX_DIR = os.path.join(ROOT, "examples")


def _read(name: str) -> str:
    with open(os.path.join(EX_DIR, name), encoding="utf-8") as f:
        return f.read()


def test_contract_risks_detected():
    text = _read("contract_sample.txt")
    risks = ContractRuleEngine().analyze(text)
    assert len(risks) > 0, "合同样例应检测到风险"
    high = [r for r in risks if r.risk_level == "High"]
    assert len(high) >= 2, f"应至少检出2个高风险，实际：{[r.issue for r in risks]}"
    # 具体命中验证
    issues = " ".join(r.issue for r in risks)
    assert "付款" in issues, "应命中付款周期不明确"
    assert "不对等" in issues or "免除" in issues, "应命中违约责任不对等"


def test_tender_requirements_and_risks():
    text = _read("tender_sample.md")
    engine = TenderRuleEngine()
    risks, reqs = engine.analyze(text)
    assert len(reqs) >= 3, f"招标样例应抽取到多条要求，实际：{len(reqs)}"
    # 样例刻意未写投标截止时间 → 应命中 HIGH
    assert any(r.risk_level == "High" for r in risks), "招标应命中缺失关键节点风险"


def test_technical_chapters_and_security():
    text = _read("tech_sample.md")
    engine = TechnicalRuleEngine()
    chapters = engine.chapter_checks(text)
    missing = [c.chapter for c in chapters if not c.present]
    assert len(missing) >= 1, "技术方案应缺失部分章节"
    sec = engine.analyze_security(text)
    perf = engine.analyze_performance(text)
    assert len(sec) >= 1, "应检出硬编码凭据/HTTP 等安全问题"
    assert len(perf) >= 1, "应检出 SELECT * 等性能风险"


if __name__ == "__main__":
    test_contract_risks_detected()
    test_tender_requirements_and_risks()
    test_technical_chapters_and_security()
    # --- real findings (printed only when run directly) ---
    print("\n[合同样例] 命中风险：")
    for r in ContractRuleEngine().analyze(_read("contract_sample.txt")):
        print(f"  {r.risk_level:6} | {r.category:8} | {r.issue}")
    t_risks, t_reqs = TenderRuleEngine().analyze(_read("tender_sample.md"))
    print(f"\n[招标样例] 抽取要求 {len(t_reqs)} 项，命中风险 {len(t_risks)} 项：")
    for r in t_risks:
        print(f"  {r.risk_level:6} | {r.issue}")
    te = TechnicalRuleEngine()
    chs = te.chapter_checks(_read("tech_sample.md"))
    miss = [c.chapter for c in chs if not c.present]
    print(f"\n[技术方案样例] 缺失章节：{miss}")
    print(f"  安全问题 {len(te.analyze_security(_read('tech_sample.md')))} 项，"
          f"性能风险 {len(te.analyze_performance(_read('tech_sample.md')))} 项")
    print("\nAll rule-engine tests passed.")
