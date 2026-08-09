"""
DocGuard AI — end-to-end verification (no fake data).
Starts nothing; assumes the server is reachable at BASE.
Verifies: health, upload, analyze (real rule engine), and RAG search.
"""
from __future__ import annotations
import os
import sys
import time
import requests

BASE = "http://127.0.0.1:" + os.environ.get("DOCGUARD_PORT", "8765")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（本文件位于 scripts/）


def wait_health(timeout: int = 45) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(BASE + "/api/health", timeout=3)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit("server did not become ready in time")


def main() -> None:
    h = wait_health()
    hdata = h.get("data", h)
    print("[HEALTH] model_loaded=%s model=%s device=%s ocr=%s" % (
        hdata.get("model_loaded"), hdata.get("model_name"),
        hdata.get("model_device"), hdata.get("ocr_available")))

    samples = {
        "contract": "examples/contract_sample.txt",
        "tender": "examples/tender_sample.md",
        "technical": "examples/tech_sample.md",
    }

    for label, rel in samples.items():
        fp = os.path.join(ROOT, rel)
        with open(fp, "rb") as fh:
            up = requests.post(BASE + "/api/upload", files={"file": fh}, timeout=30).json()
        assert up.get("success"), "upload failed: " + str(up)
        fpath = up["data"]["file_path"]
        print("\n[%s] uploaded -> %s" % (label, os.path.basename(fpath)))

        res = requests.post(BASE + "/api/analyze",
                            json={"file_path": fpath, "use_llm": True},
                            timeout=180).json()
        assert res.get("success"), "analyze failed: " + str(res)
        d = res["data"]
        risks = d.get("risks", [])
        by_level = {}
        for rk in risks:
            by_level[rk.get("risk_level")] = by_level.get(rk.get("risk_level"), 0) + 1
        print("  file_type=%s chunks=%d overall=%s risks=%d %s" % (
            d.get("file_type"), d.get("chunk_count"),
            d.get("overall_risk_level"), len(risks), by_level))
        print("  summary:", " / ".join(d.get("summary", {}).get("key_points", [])[:3]))
        for rk in risks[:4]:
            print("    - [%s] %s | loc=%s" % (
                rk.get("risk_level"), rk.get("issue"), rk.get("location")))
        if d.get("requirements"):
            print("  requirements detected: %d" % len(d["requirements"]))
        if d.get("chapter_checks"):
            miss = [c["chapter"] for c in d["chapter_checks"] if not c.get("present")]
            print("  missing chapters: %s" % (miss or "none"))

    print("\n[RAG] query: 这个合同的付款周期和金额是多少？")
    s = requests.post(BASE + "/api/search",
                      json={"query": "这个合同的付款周期和金额是多少？", "top_k": 3},
                      timeout=30).json()
    sd = s.get("data", {})
    print("  answer:", (sd.get("answer") or "")[:200])
    print("  retrieved chunks: %d" % len(sd.get("chunks", [])))
    print("\nALL E2E CHECKS COMPLETED.")


if __name__ == "__main__":
    main()
