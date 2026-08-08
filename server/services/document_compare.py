"""
Document comparison service.

Compares two versions of a document and produces a structured diff:
  * Line-level diff using Python's difflib
  * Semantic change detection (added / removed / modified clauses)
  * Risk-relevant change highlighting (e.g. amount, date, liability changes)

No model is required for the diff itself; the LLM (when available) can
summarize the impact of changes.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Tuple

from server.models.schemas import ComparisonResult, DiffSegment
from server.services.document_parser import parse_document
from server.services.security import get_logger

logger = get_logger("compare")


def compare_documents(path_a: Path, path_b: Path, ocr_service=None) -> ComparisonResult:
    """Parse two documents and produce a structured comparison."""
    doc_a = parse_document(path_a, ocr_service=ocr_service)
    doc_b = parse_document(path_b, ocr_service=ocr_service)

    text_a = doc_a.full_text
    text_b = doc_b.full_text

    segments = _diff_texts(text_a, text_b)
    change_count = sum(1 for s in segments if s.type != "unchanged")

    # Build a plain-language summary of meaningful changes.
    summary = _summarize_changes(text_a, text_b, segments, path_a.name, path_b.name)

    return ComparisonResult(
        document_a=path_a.name,
        document_b=path_b.name,
        segments=segments,
        summary=summary,
        change_count=change_count,
    )


def _diff_texts(text_a: str, text_b: str) -> List[DiffSegment]:
    """Produce segment-level diff using SequenceMatcher on lines."""
    lines_a = _split_lines(text_a)
    lines_b = _split_lines(text_b)

    matcher = SequenceMatcher(None, lines_a, lines_b, autojunk=False)
    segments: List[DiffSegment] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        block_a = "\n".join(lines_a[i1:i2]).strip()
        block_b = "\n".join(lines_b[j1:j2]).strip()
        if tag == "equal":
            continue
        segments.append(DiffSegment(
            type={
                "replace": "modified",
                "delete": "removed",
                "insert": "added",
            }.get(tag, tag),
            text_a=block_a[:500],
            text_b=block_b[:500],
            location_a=_locator(block_a),
            location_b=_locator(block_b),
        ))
    return segments[:50]  # cap to avoid huge payloads


def _split_lines(text: str) -> List[str]:
    return [l.strip() for l in text.splitlines() if l.strip()]


def _locator(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"第[一二三四五六七八九十百千0-9]+[条章节款项部分][^\n]{0,30}", text)
    if m:
        return m.group(0)
    return text[:30]


def _summarize_changes(
    text_a: str, text_b: str, segments: List[DiffSegment], name_a: str, name_b: str
) -> str:
    added = sum(1 for s in segments if s.type == "added")
    removed = sum(1 for s in segments if s.type == "removed")
    modified = sum(1 for s in segments if s.type == "modified")

    parts = [
        f"对比 {name_a} 与 {name_b}：",
        f"新增 {added} 处，删除 {removed} 处，修改 {modified} 处。",
    ]

    # Detect high-signal changes: amounts, dates, parties, liability.
    signals = []
    for seg in segments:
        target = seg.text_b or seg.text_a
        if re.search(r"(?:合同金额|总价|价款|金额).{0,8}\d", target):
            signals.append("涉及金额变化")
        if re.search(r"\d{4}\s*年\s*\d{1,2}\s*月", target):
            signals.append("涉及日期/期限变化")
        if re.search(r"违约|赔偿|责任", target):
            signals.append("涉及责任条款变化")
        if re.search(r"甲方|乙方|买方|卖方", target):
            signals.append("涉及主体变化")
    uniq = list(dict.fromkeys(signals))
    if uniq:
        parts.append("重点关注：" + "；".join(uniq) + "。")
    else:
        parts.append("未检测到金额、期限、责任类高风险变化。")
    return "".join(parts)
