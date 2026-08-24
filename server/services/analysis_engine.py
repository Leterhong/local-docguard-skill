"""
Analysis engine — orchestrates the full document analysis pipeline.

Pipeline:
  1. Parse document (PDF/DOCX/TXT/MD/HTML, with OCR fallback)
  2. Classify document type (contract / tender / technical / ...)
  3. Chunk text and build embeddings + FAISS index (for RAG)
  4. Run deterministic rule engine -> evidence-backed risks
  5. If LLM available: enrich summary, synthesize findings, draft suggestions
  6. Assemble a structured DocumentAnalysis result

A progress callback lets the API layer stream real-time status to the UI.
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

from server.config import Settings
from server.models.schemas import (
    AnalysisStatus,
    DocumentAnalysis,
    DocumentSummary,
    DocumentType,
    ProgressEvent,
    RiskItem,
    RiskLevel,
)
from server.services import rules_engine
from server.services.chunker import chunk_document
from server.services.doc_classifier import classify_document
from server.services.document_parser import parse_document
from server.services.embedding_service import EmbeddingService
from server.services.llm_service import LLMService
from server.services.ocr_service import OcrService
from server.services.security import document_id_for, get_logger
from server.services.vector_store import ChunkRecord, VectorStore

logger = get_logger("engine")

ProgressCb = Callable[[ProgressEvent], None]


class AnalysisEngine:
    def __init__(
        self,
        settings: Settings,
        ocr: OcrService,
        embedder: EmbeddingService,
        llm: LLMService,
        vector_store: VectorStore,
    ):
        self.settings = settings
        self.ocr = ocr
        self.embedder = embedder
        self.llm = llm
        self.vector_store = vector_store

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------
    def analyze(
        self,
        file_path,
        doc_type_hint: Optional[DocumentType] = None,
        use_llm: bool = True,
        use_cloud: bool = False,
        progress: Optional[ProgressCb] = None,
    ) -> DocumentAnalysis:
        from pathlib import Path

        path = Path(file_path)
        doc_id = document_id_for(path)

        def report(stage: AnalysisStatus, pct: float, msg: str):
            logger.info("[%s] %s (%.0f%%)", stage.value, msg, pct * 100)
            if progress:
                progress(ProgressEvent(stage=stage, progress=pct, message=msg, document_id=doc_id))

        start = time.time()

        # 1. Parse
        report(AnalysisStatus.PARSING, 0.05, "正在解析文档")
        parsed = parse_document(path, ocr_service=self.ocr if self.ocr.available else None)
        full_text = parsed.full_text
        if not full_text.strip():
            report(AnalysisStatus.FAILED, 1.0, "文档内容为空或无法提取")
            raise ValueError("未能从文档中提取任何文本（可能是扫描件且OCR不可用）。")

        # 2. Classify
        report(AnalysisStatus.ANALYZING, 0.20, "正在识别文档类型")
        doc_type, type_scores = classify_document(full_text, hint=doc_type_hint)

        # 3. Chunk + embed + index (RAG)
        report(AnalysisStatus.CHUNKING, 0.30, "正在文本切片")
        proc = self.settings.processing
        chunks = chunk_document(
            parsed,
            chunk_size=int(proc.get("chunk_size", 500)),
            chunk_overlap=int(proc.get("chunk_overlap", 80)),
        )

        report(AnalysisStatus.EMBEDDING, 0.45, "正在生成向量嵌入")
        if chunks:
            vectors = self.embedder.encode(
                [c.text for c in chunks], show_progress=False
            )
            records = [
                ChunkRecord(
                    chunk_id=c.chunk_id,
                    document_id=doc_id,
                    file_name=path.name,
                    text=c.text,
                    page=c.page,
                    section=c.section,
                    metadata={"char_start": c.char_start, "char_end": c.char_end},
                )
                for c in chunks
            ]
            report(AnalysisStatus.INDEXING, 0.55, "正在构建向量索引")
            self.vector_store.add(records, vectors)
        else:
            vectors = None

        # 4. Rule engine
        report(AnalysisStatus.ANALYZING, 0.65, "正在执行规则审查")
        summary, risks, extras = self._run_rules(doc_type, full_text, path.name)

        # 5. LLM enrichment (local/cloud hybrid)
        llm_used = False
        llm_name = ""
        engine_notes = ""
        original_provider = self.llm.current_provider()
        provider_switched = False
        try:
            if use_cloud:
                provider_switched = self.llm.set_provider("cloud")
                if not provider_switched:
                    engine_notes = "请求云端模型被拒绝（未启用或 local_only=true），已回退到规则引擎。"

            llm_ready = self.llm.available if self.llm.current_provider() == "local" else self.llm._cloud_available

            if use_llm and llm_ready:
                provider_label = "云端" if self.llm.current_provider() == "cloud" else "本地"
                report(AnalysisStatus.LLM_REASONING, 0.80, f"{provider_label}大模型正在深度分析")
                llm_result = self._llm_enrich(doc_type, full_text, risks, summary)
                if llm_result:
                    llm_used = True
                    llm_name = self.llm.loaded_name if self.llm.current_provider() == "local" else self.llm._providers_cfg.get("cloud", {}).get("model", "")
                    # The LLM returns a narrative string; keep `summary` as the
                    # existing DocumentSummary object and store the text in
                    # summary_text (do NOT overwrite the object with a string).
                    llm_text = llm_result.get("summary", "")
                    if isinstance(llm_text, str) and llm_text:
                        summary.summary_text = llm_text
                    # Merge LLM-identified risks (only those with evidence).
                    extra = llm_result.get("extra_risks", [])
                    if extra:
                        next_id = len(risks) + 1
                        for er in extra[:10]:
                            if er.get("evidence") and er.get("issue"):
                                risks.append(RiskItem(
                                    id=f"R-{next_id:03d}",
                                    category=er.get("category", "其他"),
                                    risk_level=self._parse_level(er.get("risk_level")),
                                    issue=er["issue"][:200],
                                    location=er.get("location", "见证据"),
                                    explanation=er.get("explanation", "")[:400],
                                    suggestion=er.get("suggestion", "")[:400],
                                    evidence=er["evidence"][:300],
                                ))
                                next_id += 1
            else:
                if not engine_notes:
                    engine_notes = (
                        "未启用大模型，结果由规则引擎基于文档真实内容生成。"
                        if not llm_ready
                        else "基于规则引擎分析完成。"
                    )
        finally:
            # Always restore default local provider for the next request.
            if original_provider != self.llm.current_provider():
                self.llm.set_provider(original_provider)

        # Deduplicate risks by evidence hash.
        risks = self._dedupe_risks(risks)

        # 6. Assemble result
        report(AnalysisStatus.REPORTING, 0.92, "正在汇总分析结果")
        by_level = {"Low": 0, "Medium": 0, "High": 0}
        for r in risks:
            by_level[r.risk_level.value] = by_level.get(r.risk_level.value, 0) + 1

        result = DocumentAnalysis(
            document_id=doc_id,
            file_name=path.name,
            file_path=str(path),
            file_type=parsed.file_type,
            file_size_bytes=path.stat().st_size,
            page_count=parsed.page_count,
            char_count=parsed.char_count,
            chunk_count=len(chunks),
            language=parsed.language,
            summary=summary,
            risks=risks,
            requirements=extras.get("requirements", []),
            capability_match_score=extras.get("capability_match_score"),
            missing_capabilities=extras.get("missing_capabilities", []),
            chapter_checks=extras.get("chapter_checks", []),
            architecture_issues=extras.get("architecture_issues", []),
            security_issues=extras.get("security_issues", []),
            performance_risks=extras.get("performance_risks", []),
            overall_risk_level=rules_engine.overall_level(risks),
            risk_count_by_level=by_level,
            llm_used=llm_used,
            llm_model_name=llm_name,
            engine_notes=engine_notes or f"分析耗时 {time.time()-start:.1f}s",
        )
        report(AnalysisStatus.COMPLETED, 1.0, "分析完成")
        return result

    # ------------------------------------------------------------------
    # Rule dispatch
    # ------------------------------------------------------------------
    def _run_rules(self, doc_type: DocumentType, text: str, filename: str):
        summary = DocumentSummary(doc_type=doc_type, title=filename)
        risks: List[RiskItem] = []
        extras: Dict = {}

        if doc_type == DocumentType.CONTRACT:
            risks.extend(rules_engine.ContractRuleEngine().analyze(text))
            summary.key_points = self._extract_keypoints_contract(text)
            summary.parties = self._extract_parties(text)

        elif doc_type == DocumentType.TENDER:
            tender = rules_engine.TenderRuleEngine()
            t_risks, reqs = tender.analyze(text)
            risks.extend(t_risks)
            # Mark all as not-matched by default; the UI lets users toggle.
            for r in reqs:
                r.matched = False
            matched = sum(1 for r in reqs if r.matched)
            score = round(matched / len(reqs) * 100, 1) if reqs else 0.0
            missing = [r.requirement for r in reqs if not r.matched][:15]
            extras["requirements"] = reqs
            extras["capability_match_score"] = score
            extras["missing_capabilities"] = missing
            summary.key_points = [
                f"识别到 {len(reqs)} 项招标要求",
                f"当前匹配度 {score}%",
                f"需重点关注 {len(t_risks)} 项风险/待确认事项",
            ]

        elif doc_type == DocumentType.TECHNICAL:
            tech = rules_engine.TechnicalRuleEngine()
            sec = tech.analyze_security(text)
            perf = tech.analyze_performance(text)
            chapters = tech.chapter_checks(text)
            risks.extend(sec)
            risks.extend(perf)
            extras["chapter_checks"] = chapters
            extras["security_issues"] = sec
            extras["performance_risks"] = perf
            missing = [c.chapter for c in chapters if not c.present]
            summary.key_points = [
                f"发现 {len(sec)} 项安全问题",
                f"发现 {len(perf)} 项性能风险",
                f"缺失章节：{', '.join(missing) if missing else '无'}",
            ]

        else:
            c_engine = rules_engine.ContractRuleEngine()
            risks.extend([
                r for r in c_engine.analyze(text)
                if r.category in ("条款明确性",)
            ])
            summary.key_points = ["通用文档分析完成"]

        if not summary.title:
            summary.title = filename
        return summary, risks, extras

    def _extract_parties(self, text: str) -> List[str]:
        parties = []
        for m in __import__("re").finditer(
            r"(?:甲方|乙方|买方|卖方|发包方|承包方|出租方|承租方)[：:]\s*([^\n，。；]{2,40})",
            text,
        ):
            name = m.group(1).strip()
            if name and name not in parties:
                parties.append(name)
        return parties[:8]

    def _extract_keypoints_contract(self, text: str) -> List[str]:
        points = []
        import re
        # Amount: require a real currency marker (¥/￥/RMB), a unit (万元/元),
        # or Chinese uppercase amount — this avoids matching clause numbers
        # like "2.1" that follow "价款".
        amount = None
        m = re.search(r"[¥￥]\s*([\d,]+(?:\.\d+)?)\s*(万元|万元人民币|万人民币|元)?", text)
        if m:
            unit = m.group(2) or "元"
            amount = f"{m.group(1)} {unit}"
        if not amount:
            m = re.search(r"([\d,]{4,}(?:\.\d+)?)\s*(万元|万元人民币|万人民币|元整|元)", text)
            if m:
                amount = f"{m.group(1)} {m.group(2)}"
        if not amount:
            m = re.search(r"(人民币)?([壹贰叁肆伍陆柒捌玖拾佰仟万亿零整]+(?:万|元))", text)
            if m:
                amount = m.group(0)
        if amount:
            points.append(f"合同金额：{amount}")
        # Term: must be a labeled field followed by a colon or an actual
        # date/duration expression (年/月/日), not a section heading.
        term = None
        m = re.search(r"(?:合同期限|有效期|服务期|履约期限)[：:]\s*([^\n。；]{2,60})", text)
        if m:
            term = m.group(1).strip()
        if not term:
            m = re.search(r"(?:合同期限|有效期|服务期|履约期限)[^。；\n]{0,6}?(\d+\s*(?:年|个月|月|日|天)[^\n。；]{0,20})", text)
            if m:
                term = m.group(1).strip()
        if term:
            points.append(f"合同期限：{term}")
        pay = re.search(r"付款方式[^。；\n]{0,80}", text)
        if pay:
            points.append(f"付款约定：{pay.group(0).strip()[:80]}")
        if not points:
            points.append("合同已解析，详见风险列表")
        return points

    # ------------------------------------------------------------------
    # LLM enrichment
    # ------------------------------------------------------------------
    def _llm_enrich(self, doc_type, text, risks, summary):
        # Use a bounded sample of the text for the summary prompt.
        sample = text[:6000]
        system = (
            "你是资深企业法务与文档审查专家。基于用户提供的文档片段，"
            "输出严谨、客观、可执行的中文审查结论。只输出JSON，不要输出多余文字。"
        )
        prompt = f"""文档类型：{doc_type.value}
文档片段：
\"\"\"
{sample}
\"\"\"

已通过规则引擎识别到 {len(risks)} 项风险。请基于文档真实内容输出JSON：
{{
  "summary": "150字以内的文档摘要",
  "extra_risks": [
    {{"category":"...","risk_level":"High|Medium|Low","issue":"...","location":"...","explanation":"...","suggestion":"...","evidence":"文档原文片段"}}
  ]
}}
只报告有原文证据支持的风险，不要臆测。"""
        result = self.llm.generate_json(prompt, system=system, max_new_tokens=1024)
        if isinstance(result, dict):
            return result
        return None

    @staticmethod
    def _parse_level(val) -> RiskLevel:
        if not val:
            return RiskLevel.MEDIUM
        v = str(val).strip().lower()
        if v.startswith("high") or v == "高":
            return RiskLevel.HIGH
        if v.startswith("low") or v == "低":
            return RiskLevel.LOW
        return RiskLevel.MEDIUM

    @staticmethod
    def _dedupe_risks(risks: List[RiskItem]) -> List[RiskItem]:
        seen = set()
        out = []
        for r in risks:
            key = (r.category, r.issue, r.evidence[:60])
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        # Renumber.
        for i, r in enumerate(out, 1):
            r.id = f"R-{i:03d}"
        return out

    # ------------------------------------------------------------------
    # RAG search
    # ------------------------------------------------------------------
    def search(self, query: str, document_id: Optional[str] = None, top_k: int = 6, use_cloud: bool = False):
        from server.models.schemas import RetrievedChunk, SearchResult

        qv = self.embedder.encode_query(query)
        hits = self.vector_store.search(qv, top_k=top_k, document_id=document_id)
        chunks = [
            RetrievedChunk(
                chunk_id=rec.chunk_id,
                text=rec.text,
                page=rec.page,
                section=rec.section,
                score=score,
                metadata=rec.metadata,
            )
            for rec, score in hits
        ]
        answer = ""
        llm_used = False
        original_provider = self.llm.current_provider()
        provider_switched = False
        try:
            if use_cloud:
                provider_switched = self.llm.set_provider("cloud")

            llm_ready = self.llm.available if self.llm.current_provider() == "local" else self.llm._cloud_available

            if chunks and llm_ready:
                context = "\n\n".join(f"[片段{c.chunk_id}] {c.text}" for c in chunks[:5])
                system = "你是企业文档助手。只能依据提供的文档片段回答，回答简洁准确；若片段中无答案，明确说明。"
                prompt = f"文档片段：\n{context}\n\n问题：{query}\n请用中文回答。"
                answer = self.llm.generate(prompt, system=system, max_new_tokens=512)
                llm_used = bool(answer)
            elif chunks:
                # No LLM: return most relevant snippet as the answer.
                answer = f"（未启用大模型，以下为最相关的原文片段）\n{chunks[0].text}"
        finally:
            if original_provider != self.llm.current_provider():
                self.llm.set_provider(original_provider)

        return SearchResult(
            query=query,
            document_id=document_id,
            answer=answer,
            chunks=chunks,
            llm_used=llm_used,
        )
