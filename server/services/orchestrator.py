"""
Agentic orchestrator -- multi-step planning and tool composition.

Turns DocGuard from a set of single-shot tools into an autonomous agent:
given a goal plus document paths / a question, the local LLM plans a
sequence of tool calls, executes them one at a time, reads intermediate
results, and decides the next step until it has a final answer.

The tools the agent calls map 1:1 to the independently callable Skill
entry points under tools/ (analyze_document, search_document, check_bid,
compare_documents). This is what makes the "Skills call / are called"
composition real: DocGuard is called by other Skills via those CLI tools
and HTTP APIs, and its own agent composes the same tools internally.

When no local LLM is available the orchestrator falls back to a
deterministic planner keyed off document type and supplied inputs, so the
Skill always produces a correct result.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from server.models.schemas import DocumentType
from server.services.security import get_logger

logger = get_logger("orchestrator")

DEFAULT_MAX_STEPS = 6


@dataclass
class AgentTrace:
    step: int
    thought: str = ""
    action: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    observation: str = ""
    duration_s: float = 0.0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step,
            "thought": self.thought,
            "action": self.action,
            "args": self.args,
            "observation": self.observation,
            "duration_s": round(self.duration_s, 2),
            "error": self.error,
        }


@dataclass
class AgentResult:
    success: bool
    goal: str
    answer: str
    steps: List[AgentTrace] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    llm_used: bool = False
    llm_model_name: str = ""
    planner: str = "deterministic"
    elapsed_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "goal": self.goal,
            "answer": self.answer,
            "planner": self.planner,
            "llm_used": self.llm_used,
            "llm_model_name": self.llm_model_name,
            "elapsed_s": round(self.elapsed_s, 2),
            "num_steps": len(self.steps),
            "steps": [s.to_dict() for s in self.steps],
            "artifacts": self.artifacts,
        }


class Orchestrator:
    TOOL_SPECS = [
        {
            "name": "analyze_document",
            "description": "解析审查文档，返回类型/摘要/风险/资质要求/章节完整性。需要先读懂文档时先调用。",
            "args": {"file_path": "本地文档路径", "doc_type_hint": "contract|tender|technical|general(可选)"},
        },
        {
            "name": "check_bid_qualification",
            "description": "招标资质要求与投标方资格逐项比对，给出结论/得分/废标硬门槛。需要已分析的招标doc_id与投标方资格。",
            "args": {"document_id": "analyze返回的招标文档id", "profile_text": "投标方资格描述"},
        },
        {
            "name": "compare_versions",
            "description": "对比文档两个版本，输出新增/删除/修改片段及高风险变化。",
            "args": {"file_path_a": "旧版本路径", "file_path_b": "新版本路径"},
        },
        {
            "name": "search_knowledge",
            "description": "在已分析文档中检索并回答具体问题(RAG)，用户有明确提问/需定位条款时使用。",
            "args": {"query": "问题", "document_id": "可选，限定文档id"},
        },
        {
            "name": "finish",
            "description": "完成任务，输出最终结论。",
            "args": {"answer": "最终中文结论"},
        },
    ]

    def __init__(self, container):
        self.container = container
        self._tools: Dict[str, Callable[..., Dict[str, Any]]] = {
            "analyze_document": self._tool_analyze,
            "check_bid_qualification": self._tool_check_bid,
            "compare_versions": self._tool_compare,
            "search_knowledge": self._tool_search,
        }

    def run(self, goal, file_paths=None, question="", profile_text="",
            doc_type_hint=None, use_llm=True):
        started = time.time()
        file_paths = file_paths or []
        llm = self.container.llm
        llm_ready = use_llm and bool(getattr(llm, "available", False))
        model_name = ""
        if llm_ready:
            try:
                model_name = (llm.info() or {}).get("loaded_name") or getattr(llm, "name", "")
            except Exception:  # noqa: BLE001
                model_name = getattr(llm, "name", "")

        ctx = {
            "goal": goal, "file_paths": file_paths, "question": question,
            "profile_text": profile_text, "doc_type_hint": doc_type_hint,
            "use_llm": use_llm,
            "doc_ids": {}, "last_analysis": None, "artifacts": {},
        }
        steps: List[AgentTrace] = []
        try:
            if llm_ready:
                answer = self._run_repl(llm, ctx, steps)
                planner = "llm"
            else:
                answer = self._run_deterministic(ctx, steps)
                planner = "deterministic"
        except Exception as exc:  # noqa: BLE001
            logger.exception("Orchestrator failed")
            return AgentResult(
                success=False, goal=goal, answer=f"编排执行失败：{exc}",
                steps=steps, artifacts=ctx.get("artifacts", {}),
                llm_used=False, planner="deterministic",
                elapsed_s=time.time() - started,
            )
        return AgentResult(
            success=True, goal=goal, answer=answer, steps=steps,
            artifacts=ctx.get("artifacts", {}), llm_used=llm_ready,
            llm_model_name=model_name, planner=planner,
            elapsed_s=time.time() - started,
        )

    # ------------------------------------------------------------------
    # LLM ReAct loop
    # ------------------------------------------------------------------
    def _run_repl(self, llm, ctx, steps):
        history: List[Dict[str, str]] = []
        consecutive_failures = 0
        for idx in range(1, DEFAULT_MAX_STEPS + 1):
            prompt = self._build_planner_prompt(ctx, history)
            trace = AgentTrace(step=idx)
            t0 = time.time()
            decision = self._plan_next(llm, prompt)
            if decision is None:
                logger.warning("LLM planner output unparseable at step %d; fallback", idx)
                trace.thought = "模型规划输出无法解析，回退确定性规划。"
                trace.action = "fallback"
                trace.duration_s = time.time() - t0
                steps.append(trace)
                return self._run_deterministic(ctx, steps, skip_analyzed=True)

            trace.thought = decision.get("thought", "")
            action = (decision.get("action") or "").strip()
            args = decision.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            trace.action = action
            trace.args = args

            if action == "finish":
                answer = str(args.get("answer", "")).strip() or "已完成审查。"
                trace.observation = "任务完成。"
                trace.duration_s = time.time() - t0
                steps.append(trace)
                return answer

            handler = self._tools.get(action)
            if handler is None:
                obs = f"未知工具：{action}。可选：{', '.join(self._tools)}、finish。"
                trace.observation = obs
                trace.error = "unknown_tool"
                trace.duration_s = time.time() - t0
                steps.append(trace)
                history.append({"thought": trace.thought, "action": action, "observation": obs})
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    logger.warning("3 consecutive failures in ReAct loop; fallback")
                    return self._run_deterministic(ctx, steps, skip_analyzed=True)
                continue

            try:
                result = handler(ctx, args)
                obs = self._summarize_observation(action, result)
                trace.observation = obs
                ctx["artifacts"][action] = result
                consecutive_failures = 0
            except Exception as exc:  # noqa: BLE001
                obs = f"工具 {action} 执行失败：{exc}"
                trace.observation = obs
                trace.error = str(exc)
                logger.warning("Tool %s failed: %s", action, exc)
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    logger.warning("3 consecutive tool failures in ReAct loop; fallback")
                    trace.duration_s = time.time() - t0
                    steps.append(trace)
                    return self._run_deterministic(ctx, steps, skip_analyzed=True)
            trace.duration_s = time.time() - t0
            steps.append(trace)
            history.append({"thought": trace.thought, "action": action, "observation": obs})
        return self._synthesize_from_artifacts(ctx)

    def _plan_next(self, llm, prompt):
        try:
            raw = llm.generate(prompt, max_new_tokens=400)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM planner call failed: %s", exc)
            return None
        if not raw:
            return None
        return self._extract_json(raw)

    @staticmethod
    def _extract_json(text):
        text = (text or "").strip()
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else None
        except (json.JSONDecodeError, ValueError):
            pass
        start = text.find("{")
        if start < 0:
            return None
        depth = 0
        end = -1
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            return None
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None

    def _build_planner_prompt(self, ctx, history):
        tool_lines = []
        for spec in self.TOOL_SPECS:
            args = ", ".join(f"{k}: {v}" for k, v in spec["args"].items())
            tool_lines.append(f"- {spec['name']}({args}) -- {spec['description']}")
        tools_block = "\n".join(tool_lines)
        hist_block = ""
        if history:
            lines = []
            for h in history:
                lines.append(f"思考：{h.get('thought','')}")
                lines.append(f"行动：{h.get('action','')}")
                lines.append(f"观察：{h.get('observation','')}")
            hist_block = "\n已执行步骤：\n" + "\n".join(lines) + "\n"
        files = ctx.get("file_paths") or []
        files_line = "\n".join(f"  - {p}" for p in files) if files else "  （未提供）"
        return f"""你是本地文档审查智能体的规划器。只能调用以下本地工具，数据不出本机：

{tools_block}

用户目标：{ctx.get('goal','')}
待审查文件：
{files_line}
用户提问：{ctx.get('question') or '（无）'}
投标方资格：{ctx.get('profile_text') or '（无）'}
文档类型提示：{ctx.get('doc_type_hint') or '（自动识别）'}
{hist_block}
根据已有观察决定下一步。只输出一个JSON对象，不要输出解释文字：
{{"thought":"为什么这么做","action":"工具名或finish","args":{{...}}}}
信息足够时用 finish，args.answer 给出中文最终结论。"""

    # ------------------------------------------------------------------
    # Deterministic planner
    # ------------------------------------------------------------------
    def _run_deterministic(self, ctx, steps, skip_analyzed=False):
        files = list(ctx.get("file_paths") or [])
        question = (ctx.get("question") or "").strip()
        profile = (ctx.get("profile_text") or "").strip()
        hint = ctx.get("doc_type_hint")

        analyses = []
        if not (skip_analyzed and ctx.get("last_analysis")):
            for path in files:
                trace = AgentTrace(step=len(steps) + 1, action="analyze_document",
                                   args={"file_path": path})
                t0 = time.time()
                try:
                    res = self._tool_analyze(ctx, {"file_path": path, "doc_type_hint": hint})
                    analyses.append(res)
                    ctx.setdefault("artifacts", {})["analyze_document"] = res
                    trace.observation = self._summarize_observation("analyze_document", res)
                except Exception as exc:  # noqa: BLE001
                    trace.error = str(exc)
                    trace.observation = f"分析失败：{exc}"
                trace.duration_s = time.time() - t0
                steps.append(trace)

        last = ctx.get("last_analysis")
        doc_type = (last or {}).get("doc_type") if last else None
        if not doc_type and analyses:
            doc_type = analyses[-1].get("doc_type")

        # Tender + bidder profile -> qualification self-check.
        if doc_type == DocumentType.TENDER.value and profile and last and last.get("document_id"):
            trace = AgentTrace(step=len(steps) + 1, action="check_bid_qualification",
                               args={"document_id": last["document_id"]})
            t0 = time.time()
            try:
                res = self._tool_check_bid(
                    ctx, {"document_id": last["document_id"], "profile_text": profile}
                )
                ctx["artifacts"]["check_bid_qualification"] = res
                trace.observation = self._summarize_observation("check_bid_qualification", res)
            except Exception as exc:  # noqa: BLE001
                trace.error = str(exc)
                trace.observation = f"资格自检失败：{exc}"
            trace.duration_s = time.time() - t0
            steps.append(trace)

        # Exactly two files -> version comparison (contract/general revisions).
        if len(files) == 2:
            trace = AgentTrace(step=len(steps) + 1, action="compare_versions",
                               args={"file_path_a": files[0], "file_path_b": files[1]})
            t0 = time.time()
            try:
                res = self._tool_compare(
                    ctx, {"file_path_a": files[0], "file_path_b": files[1]}
                )
                ctx["artifacts"]["compare_versions"] = res
                trace.observation = self._summarize_observation("compare_versions", res)
            except Exception as exc:  # noqa: BLE001
                trace.error = str(exc)
                trace.observation = f"版本对比失败：{exc}"
            trace.duration_s = time.time() - t0
            steps.append(trace)

        # Explicit question -> RAG search against the most recent doc.
        if question and last and last.get("document_id"):
            trace = AgentTrace(step=len(steps) + 1, action="search_knowledge",
                               args={"query": question, "document_id": last["document_id"]})
            t0 = time.time()
            try:
                res = self._tool_search(
                    ctx, {"query": question, "document_id": last["document_id"]}
                )
                ctx["artifacts"]["search_knowledge"] = res
                trace.observation = self._summarize_observation("search_knowledge", res)
            except Exception as exc:  # noqa: BLE001
                trace.error = str(exc)
                trace.observation = f"检索失败：{exc}"
            trace.duration_s = time.time() - t0
            steps.append(trace)

        return self._synthesize_from_artifacts(ctx)

    # ------------------------------------------------------------------
    # Tool handlers (wrap the engine / services; return plain dicts)
    # ------------------------------------------------------------------
    def _tool_analyze(self, ctx, args):
        path = args.get("file_path")
        if not path:
            # LLM may omit it when only one file exists.
            files = ctx.get("file_paths") or []
            if len(files) == 1:
                path = files[0]
        # Robustness: the LLM planner sometimes re-emits a truncated or
        # hallucinated file_path (e.g. "07a4b222_sample.txt" when the real
        # upload is "07a4b224_sample.txt"). When the literal path does not
        # exist, resolve against the originally uploaded file_paths so the
        # run still succeeds.
        if path and not Path(path).expanduser().resolve().exists():
            candidates = ctx.get("file_paths") or []
            by_base = {Path(p).name: p for p in candidates}
            base = Path(path).name
            if base in by_base:
                path = by_base[base]
            else:
                # Fuzzy fallback: match on the non-hash suffix of the
                # filename (strip the "<hex>_" upload prefix) or, when a
                # single candidate exists, prefer it outright.
                suffix = base.split("_", 1)[1] if "_" in base else base
                matches = [p for p in candidates if Path(p).name.endswith(suffix)]
                if len(matches) == 1:
                    path = matches[0]
                elif len(candidates) == 1:
                    path = candidates[0]
        if not path:
            raise ValueError("analyze_document 需要 file_path")
        # engine.analyze expects a DocumentType enum (or None), but the LLM
        # planner may pass a plain string like "tender".
        hint = args.get("doc_type_hint")
        if isinstance(hint, str):
            hint = hint.strip().lower() or None
            if hint:
                try:
                    hint = DocumentType(hint)
                except ValueError:
                    hint = None
        result = self.container.engine.analyze(
            path,
            doc_type_hint=hint,
            use_llm=ctx.get("use_llm", True),
        )
        # Register the analysis in the API-level cache so later steps
        # (bid check, RAG search) can reference it by document_id, exactly
        # like an external caller would after POST /api/analyze.
        from server.api.analyze import _analysis_cache
        _analysis_cache[result.document_id] = result
        public = result.to_public_dict()
        ctx["doc_ids"][path] = result.document_id
        ctx["last_analysis"] = {
            "document_id": result.document_id,
            "file_name": result.file_name,
            "doc_type": result.summary.doc_type.value,
            "risk_count": len(result.risks),
            "high_risk_count": result.risk_count_by_level.get("High", 0),
            "requirement_count": len(result.requirements),
        }
        return public

    def _tool_check_bid(self, ctx, args):
        from server.services.bid_matcher import BidMatcher
        from server.services.rules_engine import TenderRuleEngine
        from server.api.analyze import _analysis_cache

        document_id = args.get("document_id")
        profile = args.get("profile_text") or ctx.get("profile_text") or ""
        if not document_id:
            docs = ctx.get("doc_ids") or {}
            if docs:
                document_id = next(iter(docs.values()))
        if not document_id:
            raise ValueError("check_bid_qualification 需要 document_id（先 analyze_document）")
        if not profile:
            raise ValueError("check_bid_qualification 需要 profile_text")

        cached = _analysis_cache.get(document_id)
        if not cached:
            raise ValueError("招标文档分析结果已过期，请重新 analyze_document")
        requirements = list(cached.requirements)
        if not requirements:
            # Re-extract on the fly if the cached doc carried none.
            from pathlib import Path

            from server.services.document_parser import parse_document
            doc = parse_document(Path(str(cached.file_path)))
            requirements = TenderRuleEngine().extract_requirements(doc.full_text)

        matcher = BidMatcher(llm_service=self.container.llm if getattr(self.container.llm, "available", False) else None)
        result = matcher.evaluate(requirements, profile)
        result["tender_name"] = cached.file_name
        return result

    def _tool_compare(self, ctx, args):
        from pathlib import Path

        from server.services.document_compare import compare_documents
        a = args.get("file_path_a")
        b = args.get("file_path_b")
        if not a or not b:
            files = ctx.get("file_paths") or []
            if len(files) == 2:
                a, b = files[0], files[1]
        if not a or not b:
            raise ValueError("compare_versions 需要 file_path_a 和 file_path_b")
        ocr = self.container.ocr if self.container.ocr.available else None
        # compare_documents works on Path objects (it reads .name/.suffix).
        result = compare_documents(Path(str(a)), Path(str(b)), ocr_service=ocr)
        return result.model_dump(mode="json")

    def _tool_search(self, ctx, args):
        query = (args.get("query") or ctx.get("question") or "").strip()
        document_id = args.get("document_id")
        if not query:
            raise ValueError("search_knowledge 需要 query")
        if not document_id:
            last = ctx.get("last_analysis") or {}
            document_id = last.get("document_id")
        result = self.container.engine.search(
            query=query, document_id=document_id, top_k=6
        )
        return result.model_dump(mode="json")

    # ------------------------------------------------------------------
    # Observation / answer shaping
    # ------------------------------------------------------------------
    @staticmethod
    def _summarize_observation(action, result):
        if action == "analyze_document":
            s = result.get("summary", {})
            risks = result.get("risk_count_by_level", {})
            return (
                f"已分析 {result.get('file_name','')}，类型={s.get('doc_type','')}，"
                f"风险 {len(result.get('risks',[]))} 项"
                f"（High {risks.get('High',0)}），"
                f"资质要求 {len(result.get('requirements',[]))} 项，"
                f"doc_id={result.get('document_id','')}"
            )
        if action == "check_bid_qualification":
            gaps = result.get("blocking_gaps", [])
            return (
                f"资格结论={result.get('verdict','')}，得分={result.get('score')}，"
                f"废标级硬门槛缺口 {len(gaps)} 项"
            )
        if action == "compare_versions":
            segs = result.get("segments", []) or []
            added = sum(1 for s in segs if s.get("type") == "added")
            removed = sum(1 for s in segs if s.get("type") == "removed")
            modified = sum(1 for s in segs if s.get("type") == "modified")
            return (
                f"版本对比：新增 {added} / 删除 {removed} / 修改 {modified} 处。"
                f"{result.get('summary','')}"
            )
        if action == "search_knowledge":
            ans = (result.get("answer") or "").strip()
            return f"检索回答：{ans[:160]}" if ans else "检索到相关片段，但无生成答案。"
        return "完成。"

    def _synthesize_from_artifacts(self, ctx):
        arts = ctx.get("artifacts", {})
        parts = []
        an = arts.get("analyze_document")
        if an:
            s = an.get("summary", {})
            risks = an.get("risk_count_by_level", {})
            parts.append(
                f"文档《{an.get('file_name','')}》（{s.get('doc_type','')}）审查完成："
                f"共 {len(an.get('risks',[]))} 项风险，其中高危 {risks.get('High',0)} 项。"
            )
            hp = [r for r in an.get("risks", []) if r.get("risk_level") == "High"][:3]
            if hp:
                parts.append("主要高危问题：" + "；".join(r.get("issue", "") for r in hp) + "。")
        bid = arts.get("check_bid_qualification")
        if bid:
            parts.append(
                f"招投标资格自检：{bid.get('verdict','')}，得分 {bid.get('score')}，"
                f"废标级硬门槛 {len(bid.get('blocking_gaps',[]))} 项。"
            )
        cmp_ = arts.get("compare_versions")
        if cmp_:
            segs = cmp_.get("segments", []) or []
            added = sum(1 for s in segs if s.get("type") == "added")
            removed = sum(1 for s in segs if s.get("type") == "removed")
            modified = sum(1 for s in segs if s.get("type") == "modified")
            parts.append(
                f"版本对比：新增 {added}、删除 {removed}、修改 {modified} 处。"
                f"{cmp_.get('summary','')}"
            )
        sch = arts.get("search_knowledge")
        if sch and sch.get("answer"):
            parts.append(f"针对问题的回答：{sch['answer']}")
        if not parts:
            return "已完成处理，但未产生可总结的结果。"
        return "\n".join(parts)
