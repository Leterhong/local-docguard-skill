"""
Bid qualification matcher.

Given the requirements extracted from a tender document and a free-text
description of the bidder's own qualifications (company profile, past
projects, certificates, registered capital, team...), automatically
decides for each requirement whether the bidder meets it, and produces:
  * per-requirement match status (matched / uncertain / missing)
  * overall capability match score
  * a go / no-go verdict with the blocking gaps.

The matching is a hybrid:
  1. Deterministic layer (no model needed):
       - registered capital numeric comparison
       - year / past-project count comparison
       - certificate keyword presence (ISO/CMMI/high-tech...)
  2. Semantic layer (when a local LLM is available):
       - the model judges whether the profile text satisfies each
         requirement, returning strict JSON {matched, confidence, reason}.
       - results only upgrade "uncertain" items; a deterministic
         "matched" is never downgraded by the model.

Everything runs locally; the qualification profile never leaves the
machine.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from server.models.schemas import RequirementItem
from server.services.security import get_logger

logger = get_logger("bid_matcher")

# A requirement is considered a "hard gate" (kills the bid if the bidder
# cannot satisfy it) when its category or text matches these. These are
# statutory/eligibility conditions the bidder must *possess*. Note that
# bid bond (保证金) is NOT a hard gate: it is an action the bidder can
# perform, not a qualification they own.
_HARD_GATE_CATEGORIES = {"资质要求", "认证要求", "财务要求", "人员要求"}
_HARD_GATE_KEYWORDS = [
    "必须", "应当具备", "须具有", "须拥有", "应具有", "应拥有",
    "准入", "许可证", "资质证书", "注册资本",
]

# A bid bond / delivery / technical-spec requirement is not something the
# bidder "owns"; it should not be auto-flagged as a blocking gap from the
# profile alone. Personnel certificates ARE hard gates (the named PM must
# hold them), so they stay in _HARD_GATE_CATEGORIES.
_SOFT_CATEGORIES = {"保证金", "交付要求", "技术要求"}
_SOFT_KEYWORDS = ["保证金", "保函", "交付期", "工期", "交货期", "投标截止"]

# Professional / personnel certificates.
_PERSON_CERT_MAP: Dict[str, List[str]] = {
    "信息系统项目管理师": ["信息系统项目管理师", "高级项目经理", "系统集成项目管理工程师(高级)"],
    "PMP": ["pmp", "项目管理专业人士"],
    "一级建造师": ["一级建造师", "一建"],
    "二级建造师": ["二级建造师", "二建"],
    "注册会计师": ["注册会计师", "cpa"],
    "法律职业资格": ["法律职业资格", "律师执业证"],
}

# Certificate / qualification keyword library. A requirement mentioning
# one of these is satisfied if the profile also mentions it (or a
# recognized equivalent/superior certificate).
_CERT_MAP: Dict[str, List[str]] = {
    "ISO9001": ["iso9001", "iso 9001", "质量管理体系认证"],
    "ISO27001": ["iso27001", "iso 27001", "信息安全管理体系认证"],
    "ISO20000": ["iso20000", "iso 20000", "it服务管理体系认证"],
    "CMMI": ["cmmi3", "cmmi 3", "cmmi4", "cmmi5", "cmmi 级", "cmmi认证"],
    "高新技术企业": ["高新技术企业", "高企", "high-tech enterprise"],
    "软件企业": ["软件企业认定", "双软", "软件产品登记"],
    "安全生产许可证": ["安全生产许可证"],
    "涉密资质": ["涉密", "保密资质"],
    "建筑业资质": ["施工总承包", "专业承包", "建筑业企业资质"],
}


class BidMatcher:
    """Deterministic + optional-LLM qualification matcher."""

    def __init__(self, llm_service: Any = None):
        self.llm = llm_service

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------
    def evaluate(
        self,
        requirements: List[RequirementItem],
        profile_text: str,
    ) -> Dict[str, Any]:
        if not requirements:
            return {
                "total": 0,
                "matched": 0,
                "uncertain": 0,
                "missing": 0,
                "hard_gate_missing": 0,
                "score": 0.0,
                "verdict": "无招标要求可核对",
                "items": [],
                "blocking_gaps": [],
                "llm_used": False,
            }

        profile_norm = self._normalize(profile_text)

        # 1) deterministic per-item decision
        items: List[Dict[str, Any]] = []
        for req in requirements:
            decision = self._deterministic_match(req, profile_norm)
            items.append({
                "id": req.id,
                "category": req.category,
                "requirement": req.requirement,
                "hard_gate": self._is_hard_gate(req),
                "matched": decision["matched"],
                "status": decision["status"],  # matched | uncertain | missing
                "reason": decision["reason"],
                "evidence": decision.get("evidence", ""),
            })

        # 2) optional LLM semantic upgrade for "uncertain" items
        llm_used = False
        if self.llm is not None and getattr(self.llm, "available", False):
            uncertain_idx = [i for i, it in enumerate(items) if it["status"] == "uncertain"]
            if uncertain_idx:
                semantic = self._llm_judge_batch(
                    [items[i]["requirement"] for i in uncertain_idx],
                    profile_text,
                )
                for local_i, result in zip(uncertain_idx, semantic):
                    if result and result.get("matched") is True:
                        items[local_i]["status"] = "matched"
                        items[local_i]["matched"] = True
                        items[local_i]["reason"] = "本地大模型语义判断：资质可覆盖（" + result.get("reason", "")[:120] + "）"
                        llm_used = True
                    elif result and result.get("matched") is False:
                        # Keep as missing/uncertain distinction but record reason.
                        items[local_i]["reason"] = "本地大模型判断：资质未能覆盖（" + result.get("reason", "")[:120] + "）"
                        llm_used = True

        # 3) aggregate
        matched = sum(1 for it in items if it["status"] == "matched")
        missing = sum(1 for it in items if it["status"] == "missing")
        uncertain = sum(1 for it in items if it["status"] == "uncertain")
        total = len(items)
        # uncertain items count as half for scoring (so the number is honest)
        score = round((matched + uncertain * 0.5) / total * 100, 1)

        blocking = [
            it for it in items
            if it["hard_gate"] and it["status"] != "matched"
        ]
        blocking_gaps = [
            {"id": it["id"], "category": it["category"], "requirement": it["requirement"],
             "reason": it["reason"]}
            for it in blocking
        ]

        # 4) verdict
        if blocking:
            verdict = (
                f"不建议投标：存在 {len(blocking)} 项硬性资质门槛未满足，"
                "属于废标风险项，须先补齐或联合体合作。"
            )
        elif missing == 0 and uncertain == 0:
            verdict = "可投标：所有已识别要求均已满足，资质完整。"
        elif score >= 80:
            verdict = (
                f"可重点准备：满足度 {score}%，{uncertain} 项待人工确认，"
                "无硬性门槛缺口。"
            )
        else:
            verdict = (
                f"谨慎评估：满足度 {score}%，缺失 {missing} 项、待确认 {uncertain} 项，"
                "需衡量补齐成本与中标概率。"
            )

        return {
            "total": total,
            "matched": matched,
            "uncertain": uncertain,
            "missing": missing,
            "hard_gate_missing": len(blocking),
            "score": score,
            "verdict": verdict,
            "items": items,
            "blocking_gaps": blocking_gaps,
            "llm_used": llm_used,
        }

    # ------------------------------------------------------------------
    # Deterministic matching
    # ------------------------------------------------------------------
    def _deterministic_match(self, req: RequirementItem, profile_norm: str) -> Dict[str, Any]:
        text = req.requirement
        text_norm = self._normalize(text)

        # Soft/actionable requirements (bond, delivery, tech spec) are not
        # things the profile "owns"; report them as to-confirm, never as a
        # deterministic gap.
        if req.category in _SOFT_CATEGORIES or any(k in text for k in _SOFT_KEYWORDS):
            return self._uncertain("属投标动作/履约条款，需在投标方案中确认响应（非资质缺口）")

        # (a) registered capital: requirement number vs profile number
        cap = self._extract_capital(text_norm)
        if cap is not None:
            prof_cap = self._extract_max_capital(profile_norm)
            if prof_cap is not None and prof_cap >= cap:
                return self._ok(f"注册资本 {prof_cap} 万元 ≥ 要求 {cap} 万元")
            if prof_cap is not None:
                return self._miss(
                    f"注册资本 {prof_cap} 万元 < 要求 {cap} 万元",
                    evidence=f"要求：{text[:80]}",
                )
            return self._uncertain("要求注册资本，但资质材料中未识别到注册资本金额")

        # (b) number of similar past projects (check before years, because
        # "近三年至少2个项目" is a project-count requirement, not an XP-year one)
        proj = self._extract_project_count(text_norm)
        if proj is not None:
            prof_proj = self._extract_project_count(profile_norm)
            if prof_proj is not None and prof_proj >= proj:
                return self._ok(f"类似项目业绩 {prof_proj} 个 ≥ 要求 {proj} 个")
            return self._uncertain(f"要求至少 {proj} 个类似项目业绩，资质材料未能自动核对数量")

        # (c) personnel certificate (信息系统项目管理师 / PMP / 建造师...)
        person_certs = self._required_person_certs(text_norm)
        if person_certs:
            missing = [c for c in person_certs if not self._has_cert(profile_norm, c)]
            if not missing:
                return self._ok(f"团队具备人员证书：{'、'.join(person_certs)}")
            return self._miss(
                f"缺少人员证书：{'、'.join(missing)}",
                evidence=f"要求：{text[:80]}",
            )

        # (d) years of experience (PM / company)
        years = self._extract_years(text_norm)
        if years is not None:
            prof_years = self._extract_years(profile_norm)
            if prof_years is not None and prof_years >= years:
                return self._ok(f"相关年限 {prof_years} 年 ≥ 要求 {years} 年")
            return self._uncertain(f"要求 {years} 年经验，资质材料年限未能自动核对")

        # (e) certificate keywords
        certs = self._required_certs(text_norm)
        if certs:
            missing_certs = [c for c in certs if not self._has_cert(profile_norm, c)]
            if not missing_certs:
                return self._ok(f"已具备证书：{'、'.join(certs)}")
            return self._miss(
                f"缺少证书：{'、'.join(missing_certs)}",
                evidence=f"要求：{text[:80]}",
            )

        # (f) generic keyword overlap
        overlap = self._keyword_overlap(text_norm, profile_norm)
        if overlap >= 0.6:
            return self._ok(f"资质材料中出现高度相关关键词（重合度 {int(overlap*100)}%）")

        # (g) explicit "no" / "不要求" -> matched by vacuous satisfaction
        if re.search(r"不做要求|不作要求|无[需須]要求|本项目不要求", text_norm):
            return self._ok("该条为非强制要求")

        return self._uncertain("需人工核对：未在资质材料中找到明确对应项")

    # ------------------------------------------------------------------
    # Optional LLM semantic judgement (batched)
    # ------------------------------------------------------------------
    def _llm_judge_batch(self, requirements: List[str], profile_text: str) -> List[Optional[Dict]]:
        results: List[Optional[Dict]] = [None] * len(requirements)
        # Judge in small batches to keep the prompt bounded.
        BATCH = 5
        system = (
            "你是招投标资质审核助手。根据投标方的资质材料，逐条判断其是否满足招标要求。"
            "只输出严格 JSON，形如 [{\"matched\": true/false, \"confidence\": 0.0-1.0, "
            "\"reason\": \"简短依据\"}]，不要输出任何解释或 Markdown 代码块。"
        )
        for start in range(0, len(requirements), BATCH):
            chunk = requirements[start:start + BATCH]
            req_list = "\n".join(f"{i+1}. {r}" for i, r in enumerate(chunk))
            prompt = (
                f"【投标方资质材料】\n{profile_text[:4000]}\n\n"
                f"【待核对的招标要求】\n{req_list}\n\n"
                "请逐条判断资质材料是否满足该要求，输出 JSON 数组。"
            )
            try:
                raw = self.llm.generate(prompt, system=system, max_new_tokens=800)
                parsed = self._parse_json_array(raw)
                if parsed and len(parsed) == len(chunk):
                    for j, item in enumerate(parsed):
                        results[start + j] = item
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM bid matching batch failed: %s", exc)
        return results

    @staticmethod
    def _parse_json_array(text: str) -> Optional[List[Dict]]:
        if not text:
            return None
        # strip code fences if the model added them
        text = text.strip()
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            return None
        return None

    # ------------------------------------------------------------------
    # Numeric / keyword helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize(text: str) -> str:
        if not text:
            return ""
        text = text.lower()
        text = text.replace("（", "(").replace("）", ")")
        text = re.sub(r"\s+", "", text)
        return text

    @staticmethod
    def _extract_capital(text_norm: str) -> Optional[float]:
        # e.g. 注册资本不低于人民币500万元 / 注册资本500万
        m = re.search(r"注册资本[^0-9]{0,12}?(\d+(?:\.\d+)?)\s*(万元|万元人民币|万人民币|万元整|万)", text_norm)
        if m:
            return float(m.group(1))
        # 元 form: 5000000元
        m = re.search(r"注册资本[^0-9]{0,12}?(\d{6,})\s*元", text_norm)
        if m:
            return round(float(m.group(1)) / 10000.0, 2)
        return None

    @staticmethod
    def _extract_max_capital(text_norm: str) -> Optional[float]:
        caps = [float(x) for x in re.findall(r"注册资本[^0-9]{0,12}?(\d+(?:\.\d+)?)\s*万", text_norm)]
        caps += [round(float(x) / 10000.0, 2) for x in re.findall(r"注册资本[^0-9]{0,12}?(\d{6,})\s*元", text_norm)]
        return max(caps) if caps else None

    @staticmethod
    def _extract_years(text_norm: str) -> Optional[int]:
        # N 年以上 ...经验/从业/成立/历史/项目管理经验
        m = re.search(r"(\d+)\s*年(?:及?以上)?[\s\S]{0,8}?(?:从业|经验|成立|历史|项目管理|工作)", text_norm)
        if m:
            return int(m.group(1))
        m = re.search(r"(?:近)\s*(\d+)\s*年", text_norm)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _extract_project_count(text_norm: str) -> Optional[int]:
        # at least N items/projects/cases:  至少 N 个 ... 项目/业绩/案例
        m = re.search(
            r"(?:至少|不少于|不低于)?\s*(\d+)\s*(?:个|项)[\s\S]{0,20}?(?:项目|业绩|案例|合同|工程|案例)",
            text_norm,
        )
        if m:
            return int(m.group(1))
        return None

    def _required_person_certs(self, text_norm: str) -> List[str]:
        found = []
        for cert, aliases in _PERSON_CERT_MAP.items():
            if any(a in text_norm for a in aliases):
                found.append(cert)
        return found

    def _required_certs(self, text_norm: str) -> List[str]:
        found = []
        for cert, aliases in _CERT_MAP.items():
            if any(a in text_norm for a in aliases):
                found.append(cert)
        return found

    def _has_cert(self, profile_norm: str, cert: str) -> bool:
        aliases = _CERT_MAP.get(cert, [cert.lower()])
        return any(a in profile_norm for a in aliases)

    @staticmethod
    def _keyword_overlap(req_norm: str, profile_norm: str) -> float:
        # Extract 2-4 char Chinese + ascii word tokens from requirement
        tokens = set(re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fa5]{2,4}", req_norm))
        # drop stopwords / generic verbs
        stop = {"投标人", "供应商", "应当", "具有", "具备", "要求", "提供", "相关", "以上"}
        tokens = {t for t in tokens if t not in stop and len(t) >= 2}
        if not tokens:
            return 0.0
        hit = sum(1 for t in tokens if t in profile_norm)
        return hit / len(tokens)

    @staticmethod
    def _is_hard_gate(req: RequirementItem) -> bool:
        # Soft/actionable requirements are never hard gates.
        if req.category in _SOFT_CATEGORIES:
            return False
        if any(k in req.requirement for k in _SOFT_KEYWORDS):
            return False
        if req.category in _HARD_GATE_CATEGORIES:
            return True
        return any(k in req.requirement for k in _HARD_GATE_KEYWORDS)

    # result shorthands
    @staticmethod
    def _ok(reason: str, evidence: str = "") -> Dict[str, Any]:
        return {"status": "matched", "matched": True, "reason": reason, "evidence": evidence}

    @staticmethod
    def _miss(reason: str, evidence: str = "") -> Dict[str, Any]:
        return {"status": "missing", "matched": False, "reason": reason, "evidence": evidence}

    @staticmethod
    def _uncertain(reason: str, evidence: str = "") -> Dict[str, Any]:
        return {"status": "uncertain", "matched": False, "reason": reason, "evidence": evidence}