"""
PDF format verification: generate a Chinese contract PDF, then run it
through the live server (upload -> parse -> rule analysis) to prove the
PDF parsing path produces real extracted text + risk findings.
"""
from __future__ import annotations
import os
import requests
from fpdf import FPDF

FONT = "C:/Windows/Fonts/simhei.ttf"  # Windows 系统字体；非 Windows 请改为可用的中文字体路径
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（本文件位于 scripts/）
OUT = os.path.join(ROOT, "data", "samples", "contract_verify.pdf")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

pdf = FPDF()
pdf.add_font("simhei", "", FONT, uni=True)
pdf.add_page()
pdf.set_font("simhei", size=12)
text = (
    "软件开发服务合同\n\n"
    "甲方：北京示例科技有限公司\n乙方：上海云创软件有限公司\n\n"
    "第一条 合同标的\n甲方委托乙方开发企业协同管理平台一套，合同总金额人民币伍拾万元整。\n\n"
    "第三条 付款方式\n本项目付款的具体时间与比例由双方另行协商确定，未约定明确付款周期。\n\n"
    "第四条 交付与验收\n若发生违约，相关责任由乙方独自承担，甲方不承担任何违约责任，乙方不得向甲方索赔。\n\n"
    "第五条 保密条款\n双方应对在合作中知悉的商业秘密予以保密。\n"
)
pdf.multi_cell(0, 8, text)
pdf.output(OUT)
print("PDF generated: %s (%d bytes)" % (OUT, os.path.getsize(OUT)))

BASE = "http://127.0.0.1:" + os.environ.get("DOCGUARD_PORT", "8765")
with open(OUT, "rb") as f:
    up = requests.post(BASE + "/api/upload", files={"file": f}, timeout=30).json()
assert up.get("success"), "upload failed: %s" % up
fpath = up["data"]["file_path"]

res = requests.post(BASE + "/api/analyze",
                    json={"file_path": fpath, "use_llm": True}, timeout=180).json()
assert res.get("success"), "analyze failed: %s" % res
d = res["data"]
print("file_type=%s pages=%s chars=%s chunks=%d" % (
    d.get("file_type"), d.get("page_count"), d.get("char_count"), d.get("chunk_count")))
print("overall=%s risks=%d" % (d.get("overall_risk_level"), len(d.get("risks", []))))
for rk in d.get("risks", [])[:6]:
    print("  - [%s] %s" % (rk.get("risk_level"), rk.get("issue")))
print("\nPDF PATH VERIFIED: real text extracted and analyzed.")
