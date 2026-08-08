"""
Document type classifier.

Determines whether a document is a contract, tender, technical proposal,
PRD, policy, or general document using structural heuristics on the full
text. No model required; deterministic and explainable.
"""
from __future__ import annotations

import re
from typing import Tuple

from server.models.schemas import DocumentType


# Keyword profiles per document type. Each entry maps to a score.
_KEYWORDS = {
    DocumentType.CONTRACT: [
        "甲方", "乙方", "合同", "协议", "条款", "违约责任", "付款", "支付",
        "签订", "履行", "争议解决", "管辖权", "保密条款", "合同编号",
        "agreement", "party a", "party b", "liability", "termination",
        "governing law", "indemnif", "whereas",
    ],
    DocumentType.TENDER: [
        "招标", "投标", "投标人", "招标人", "中标", "标书", "评标",
        "招标公告", "资格要求", "技术要求", "商务要求", "投标文件",
        "bid", "tender", "proposal submission", "invitation to bid",
        "eligibility", "evaluation criteria",
    ],
    DocumentType.TECHNICAL: [
        "架构", "系统设计", "技术方案", "接口", "API", "数据库",
        "部署", "性能", "安全", "加密", "认证", "高可用", "扩展性",
        "微服务", "负载均衡", "缓存", "中间件", "architecture",
        "scalability", "latency", "throughput", "deployment",
    ],
    DocumentType.PRD: [
        "需求", "用户故事", "功能", "用例", "原型", "优先级",
        "验收标准", "产品需求", "PRD", "user story", "acceptance criteria",
        "feature", "requirement",
    ],
    DocumentType.POLICY: [
        "制度", "规定", "管理办法", "流程", "规范", "准则", "章程",
        "第.*条", "职责", "实施", "生效", "policy", "regulation",
        "guideline", "procedure",
    ],
}


def classify_document(text: str, hint: DocumentType | None = None) -> Tuple[DocumentType, dict]:
    """Return (doc_type, scores). Hint, if provided, is used as a prior."""
    if hint is not None:
        return hint, {hint.value: 1.0}

    if not text:
        return DocumentType.GENERAL, {}

    sample = text[:15000].lower()
    scores: dict = {}
    for dtype, words in _KEYWORDS.items():
        score = 0.0
        for w in words:
            try:
                if re.search(re.escape(w.lower()), sample):
                    score += 1.0
            except re.error:
                continue
        if score > 0:
            scores[dtype] = score

    if not scores:
        return DocumentType.GENERAL, scores

    # Normalize and pick the best.
    best = max(scores, key=lambda k: scores[k])
    # Contract is the strongest signal — legal boilerplate is distinctive.
    return best, scores
