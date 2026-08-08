"""
Text chunking service.

Splits a parsed document into overlapping chunks suitable for embedding.
Chunk boundaries respect paragraphs and Chinese/legal clause markers to
avoid splitting a single clause across two chunks (which would hurt both
retrieval and risk localization).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import List, Optional

from server.services.document_parser import ParsedDocument, detect_section


@dataclass
class Chunk:
    chunk_id: str
    text: str
    page: Optional[int] = None
    section: str = ""
    char_start: int = 0
    char_end: int = 0


def chunk_document(
    doc: ParsedDocument,
    chunk_size: int = 500,
    chunk_overlap: int = 80,
) -> List[Chunk]:
    """Split a parsed document into chunks."""
    chunks: List[Chunk] = []
    for page in doc.pages:
        if not page.text or not page.text.strip():
            continue
        page_chunks = _split_text(
            page.text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        current_section = detect_section(page.text)
        offset = 0
        for text in page_chunks:
            start = page.text.find(text, offset)
            if start == -1:
                start = offset
            offset = start + len(text)
            section = detect_section(text) or current_section
            chunks.append(
                Chunk(
                    chunk_id=uuid.uuid4().hex[:12],
                    text=text,
                    page=page.index,
                    section=section,
                    char_start=start,
                    char_end=start + len(text),
                )
            )
    return chunks


def _split_text(text: str, chunk_size: int, chunk_overlap: int) -> List[str]:
    """Split text respecting paragraph and clause boundaries."""
    if len(text) <= chunk_size:
        return [text.strip()] if text.strip() else []

    # First split into paragraphs.
    paragraphs = re.split(r"\n\s*\n", text)
    units: List[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(para) <= chunk_size:
            units.append(para)
        else:
            # Split long paragraph on clause boundaries (Chinese legal style).
            units.extend(_split_long_paragraph(para, chunk_size))

    # Merge small units into chunks up to chunk_size, with overlap.
    chunks: List[str] = []
    current = ""
    for unit in units:
        if len(current) + len(unit) + 2 <= chunk_size:
            current = f"{current}\n\n{unit}" if current else unit
        else:
            if current.strip():
                chunks.append(current.strip())
            # Apply overlap from the tail of current.
            if chunk_overlap > 0 and current:
                tail = current[-chunk_overlap:]
                current = f"{tail}\n\n{unit}"
            else:
                current = unit
    if current.strip():
        chunks.append(current.strip())

    # Handle any chunk that still exceeds size (hard split).
    final: List[str] = []
    for c in chunks:
        if len(c) <= chunk_size:
            final.append(c)
        else:
            final.extend(_hard_split(c, chunk_size, chunk_overlap))
    return [c for c in final if c.strip()]


def _split_long_paragraph(para: str, chunk_size: int) -> List[str]:
    """Split on Chinese clause markers / sentence enders."""
    # Split after 。；！？ and numbered clauses like 第一条, 1. , （一）
    pattern = r"(?<=[。；！？\?\!])|(?<=^|\n)(?=第[一二三四五六七八九十百千0-9]+[条章节款项部分])"
    parts = re.split(pattern, para)
    result: List[str] = []
    buf = ""
    for part in parts:
        if not part:
            continue
        if len(buf) + len(part) <= chunk_size:
            buf += part
        else:
            if buf:
                result.append(buf)
            buf = part
    if buf:
        result.append(buf)
    return result or [para]


def _hard_split(text: str, chunk_size: int, overlap: int) -> List[str]:
    out = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        out.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return out
