"""
Rule engine for deterministic risk detection.

This is the analytical backbone of DocGuard AI. It does NOT use fake data
or hardcoded conclusions - it scans the actual document text for patterns
corresponding to real legal, commercial, and technical risks and produces
evidence-backed findings.

The LLM (when available) enriches explanations/suggestions, but every
risk item is grounded in a regex/keyword match against the real text.
This guarantees the Skill returns meaningful results even without a model.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from server.models.schemas import (
    ChapterCheck,
    RequirementItem,
    RiskItem,
    RiskLevel,
)


# =====================================================================
# Helpers
# =====================================================================
def _find_context(text: str, pattern: str, window: int = 90, flags: int = 0) -> List[str]:
    results = []
    for m in re.finditer(pattern, text, flags):
        start = max(0, m.start() - window)
        end = min(len(text), m.end() + window)
        results.append(text[start:end].replace("\n", " ").strip())
    return results


def _clause_location(text: str, pos: int) -> str:
    prefix = text[:max(0, pos)]
    for pat in [
        r"第[一二三四五六七八九十百千0-9]+条[^\n]{0,40}",
        r"第[一二三四五六七八九十百千0-9]+章[^\n]{0,40}",
        r"第[一二三四五六七八九十百千0-9]+节[^\n]{0,40}",
        r"^\d+(?:\.\d+)*[、.\s][^\n]{2,40}",
    ]:
        matches = list(re.finditer(pat, prefix, re.MULTILINE))
        if matches:
            return matches[-1].group(0).strip()
    return "正文"


def overall_level(risks: List[RiskItem]) -> RiskLevel:
    if any(r.risk_level == RiskLevel.HIGH for r in risks):
        return RiskLevel.HIGH
    if any(r.risk_level == RiskLevel.MEDIUM for r in risks):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


# =====================================================================
# Contract engine
# =====================================================================
class ContractRuleEngine:
    RULES: List[Tuple[str, str, RiskLevel, str, str, str, str]] = [
        ("C-PAY-001", "付款条款", RiskLevel.HIGH, "付款周期/时间不明确",
         r"付款[^。；\n]{0,40}(?:待定|另行协商|另议|协商确定|TBD|tbd)",
         "合同未明确付款具体时间节点或账期，可能导致付款拖延与争议。",
         "明确具体付款日期或账期（如验收合格后30日内），并约定逾期违约金。"),
        ("C-PAY-002", "付款条款", RiskLevel.MEDIUM, "付款比例需核查",
         r"(?:预付|定金|首付款)[^。；\n]{0,30}(\d{2,3})\s*%",
         "预付款比例过高增加买方资金风险；尾款过低削弱对交付质量的约束。",
         "建议预付款不超过30%，保留10%-20%质保金并与里程碑挂钩。"),
        ("C-LIA-001", "违约责任", RiskLevel.HIGH, "违约责任不对等或被免除",
         r"(?:违约责任|违约金|赔偿责任)[\s\S]{0,200}(?:不承担|免除|豁免|无权)",
         "出现免除/弱化一方违约责任的表述，权责不对等。",
         "明确双方违约责任，违约金比例应对等合理（如0.5‰-5‰/日）。"),
        ("C-TERM-001", "合同期限/解除", RiskLevel.HIGH, "单方解除权过于宽泛",
         r"(?:有权|可)(?:随时|单方面|无故|无需理由)[^。；\n]{0,30}(?:解除|终止)",
         "一方可无理由单方解除，合同关系不稳定。",
         "限制单方解除权，约定解除条件、通知期限及解除后结算赔偿。"),
        ("C-CONF-001", "保密条款", RiskLevel.MEDIUM, "保密条款需核查完整性",
         r"保密[\s\S]{0,120}",
         "存在保密条款，但需核查期限、范围及违约责任是否完整。",
         "明确保密范围、期限（建议终止后2-5年）及泄密赔偿责任。"),
        ("C-DISP-001", "争议解决", RiskLevel.MEDIUM, "管辖约定可能不利",
         r"(?:管辖|诉讼|仲裁)[\s\S]{0,60}(?:甲方|被告|买方|对方)(?:所在地|住所地)?",
         "约定由对方所在地管辖，增加己方维权成本。",
         "争取己方所在地或合同履行地管辖；仲裁需写明机构全称。"),
        ("C-FM-001", "不可抗力", RiskLevel.LOW, "核查不可抗力条款",
         r"不可抗力",
         "存在不可抗力条款，建议核查是否涵盖疫情、政策变化及通知义务。",
         "明确不可抗力认定、通知期限（如48小时内）及减损义务。"),
        ("C-AMB-001", "条款明确性", RiskLevel.MEDIUM, "存在模糊表述",
         r"(?:适当|合理|相应|酌情|及时|尽快|视情况|相关部门)[^\n]{0,20}",
         "使用'适当/合理/及时'等模糊词，缺乏可量化标准，易产生争议。",
         "替换为可量化标准（具体金额、天数、比例、交付物清单）。"),
        ("C-AMB-002", "条款明确性", RiskLevel.HIGH, "存在空白/待填占位项",
         r"(_{3,}|【[^】]{0,20}】|\[\s*\]|（\s*）待填|X{2,}|×{2,}|待定|待补充)",
         "存在空白下划线、待填括号或占位符，可能导致条款不成立或被恶意补填。",
         "签署前填写所有空白项；不适用条款划线标注'无'或'不适用'。"),
        ("C-AUTO-001", "自动续约", RiskLevel.MEDIUM, "存在自动续约条款",
         r"自动续(?:约|期|签)|到期(?:自动)?续",
         "到期自动续约可能使一方在未明确同意时被继续约束。",
         "明确续约需双方书面确认，并设置到期提醒与不续约通知期限。"),
        ("C-DATA-001", "数据合规", RiskLevel.HIGH, "涉及个人信息但责任未明",
         r"(?:个人信息|用户数据|隐私数据|数据收集)",
         "涉及个人信息但未明确数据安全责任，存在违反《个人信息保护法》风险。",
         "补充数据处理协议（DPA），明确目的、范围、期限、安全措施及违约责任。"),
    ]

    ESSENTIAL = {
        "合同标的/价款": r"(标的|合同金额|总价|价款|服务费|采购内容)",
        "付款条款": r"付款|支付|价款",
        "交付/验收": r"交付|验收|履行期限|工期",
        "违约责任": r"违约",
        "争议解决": r"争议|仲裁|诉讼管辖|向.{0,12}法院",
    }

    def analyze(self, text: str) -> List[RiskItem]:
        risks: List[RiskItem] = []
        idx = 1
        for rule_id, category, level, issue, pattern, expl, sugg in self.RULES:
            for ctx in _find_context(text, pattern)[:3]:
                pos = text.find(ctx[:30])
                loc = _clause_location(text, pos) if pos >= 0 else "正文"
                risks.append(RiskItem(
                    id=f"R-{idx:03d}", category=category, risk_level=level,
                    issue=issue, location=loc, explanation=expl,
                    suggestion=sugg, evidence=ctx[:300]))
                idx += 1
        for name, pat in self.ESSENTIAL.items():
            if not re.search(pat, text):
                risks.append(RiskItem(
                    id=f"R-{idx:03d}", category="合同完整性", risk_level=RiskLevel.HIGH,
                    issue=f"缺少必备条款：{name}", location="合同全文",
                    explanation=f"未检测到'{name}'约定，可能影响合同效力与可执行性。",
                    suggestion=f"补充'{name}'条款，明确双方权利义务。",
                    evidence="（未发现相关内容）"))
                idx += 1
        return risks


# =====================================================================
# Tender engine
# =====================================================================
class TenderRuleEngine:
    REQ_PATTERNS = [
        ("资质要求", r"(?:投标人|供应商|响应人)[^。；\n]{0,30}?(?:具备|具有|须拥有?|应拥有?)[^。；\n]{4,60}"),
        ("业绩要求", r"(?:近\s*\d+\s*年|类似项目|业绩)[^。；\n]{4,60}"),
        ("财务要求", r"(?:注册资本|财务状况|审计报告|营收|营业收入)[^。；\n]{4,50}"),
        ("技术要求", r"(?:技术(?:参数|要求|指标|规格)|功能要求)[\s\S]{0,80}"),
        ("交付要求", r"(?:工期|交付期|交货期|完工|实施周期|服务期)[^。；\n]{2,50}"),
        ("保证金", r"(?:投标保证金|履约保证金|质保金|保函)[^。；\n]{2,50}"),
        ("人员要求", r"(?:项目经理|负责人|团队成员)[^。；\n]{0,30}?(?:具备|具有|资格|证书)[^。；\n]{2,50}"),
        ("认证要求", r"(?:ISO|CMMI|资质证书|体系认证|认证证书|许可证|高新技术企业|信息系统项目管理师|系统集成|软件企业)[^。；\n]{0,40}"),
    ]

    def extract_requirements(self, text: str) -> List[RequirementItem]:
        # Collect (category, text) then dedup by near-containment, keeping
        # the longer/more informative wording.
        collected: List[tuple] = []
        for category, pattern in self.REQ_PATTERNS:
            for m in re.finditer(pattern, text):
                req_text = m.group(0).strip().replace("\n", " ")
                if len(req_text) < 6:
                    continue
                norm = re.sub(r"\s+", "", req_text)
                dup_i = None
                for i, (_, prev) in enumerate(collected):
                    pn = re.sub(r"\s+", "", prev)
                    if norm in pn or pn in norm:
                        dup_i = i
                        break
                if dup_i is not None:
                    cat_prev, prev = collected[dup_i]
                    if len(norm) > len(re.sub(r"\s+", "", prev)):
                        collected[dup_i] = (category, req_text)
                else:
                    collected.append((category, req_text))

        reqs: List[RequirementItem] = []
        for idx, (category, req_text) in enumerate(collected[:50], start=1):
            reqs.append(RequirementItem(
                id=f"REQ-{idx:03d}", requirement=req_text[:200],
                category=category, evidence=req_text[:200]))
        return reqs

    def analyze(self, text: str) -> Tuple[List[RiskItem], List[RequirementItem]]:
        risks: List[RiskItem] = []
        reqs = self.extract_requirements(text)
        idx = 1
        checks = [
            (r"(?:截止|递交|投标).{0,10}(?:时间|日期)|截标|开标时间",
             "招标流程", RiskLevel.HIGH, "未找到明确的投标截止/开标时间",
             "缺少关键时间节点，可能导致错过投标。", "核实并标注投标截止、开标、答疑截止时间。"),
            (r"保证金|保函|担保", "商务要求", RiskLevel.MEDIUM, "未明确保证金要求",
             "未发现保证金/保函条款，需确认是否缴纳。", "确认保证金金额、形式及退还条件。", True),
            (r"评标|评分|评审标准|打分", "评标", RiskLevel.MEDIUM, "未明确评标/评分标准",
             "缺少评分标准，难以针对性准备投标策略。", "索取评标办法及分值权重。", True),
            (r"付款|支付|价款|结算", "付款", RiskLevel.HIGH, "未明确付款方式",
             "未发现付款条款，影响现金流评估。", "确认付款节点、比例及预付款。", True),
        ]
        for pat, cat, lvl, issue, expl, sugg, *absent_flag in checks:
            is_absent = bool(absent_flag)
            found = bool(re.search(pat, text))
            if (is_absent and not found) or (not is_absent and found):
                risks.append(RiskItem(
                    id=f"R-{idx:03d}", category=cat, risk_level=lvl,
                    issue=issue, location="全文", explanation=expl,
                    suggestion=sugg, evidence="（见对应条款）" if found else "（未发现相关内容）"))
                idx += 1
        return risks, reqs


# =====================================================================
# Technical engine
# =====================================================================
class TechnicalRuleEngine:
    REQUIRED_CHAPTERS = [
        ("系统架构", r"架构|系统设计|总体设计|architecture"),
        ("技术选型", r"技术栈|技术选型|选型|technology"),
        ("接口设计", r"接口|API|interface"),
        ("数据设计", r"数据库|数据模型|数据表|存储设计"),
        ("安全设计", r"安全|加密|认证|鉴权|权限"),
        ("性能设计", r"性能|吞吐|延迟|并发|QPS|TPS"),
        ("部署方案", r"部署|运维|发布|上线|环境"),
        ("测试方案", r"测试|test|验收"),
    ]

    SECURITY = [
        (r"(?:password|passwd|pwd|密码|secret|token|api[_-]?key|access[_-]?key)\s*[:=]\s*['\"][^'\"]{4,}",
         "疑似硬编码凭据", "配置/代码中出现明文密码或密钥，存在严重隐患。",
         "使用KMS或环境变量，禁止明文提交凭据。"),
        (r"http://(?!localhost|127\.0\.0\.1)",
         "使用非加密HTTP传输", "对外通信使用HTTP明文，可能被窃听篡改。",
         "生产环境强制HTTPS/TLS。"),
        (r"(?:SELECT|INSERT|UPDATE|DELETE)[\s\S]{0,60}(?:\+|\$\{|format\(|f['\"])",
         "疑似SQL注入风险", "SQL通过字符串拼接构造，存在注入漏洞。",
         "使用参数化查询/ORM，禁止拼接用户输入。"),
        (r"eval\s*\(|exec\s*\(|os\.system|subprocess[\s\S]{0,20}shell\s*=\s*True",
         "危险函数调用", "使用eval/exec/系统命令，可能导致远程代码执行。",
         "避免危险函数；必须使用时做严格白名单校验。"),
        (r"(?:md5|sha1)\s*\(|\.encode\(\)[\s\S]{0,10}(?:md5|sha1)|DES[^A-Za-z]|RC4|ECB\s*mode",
         "使用弱加密算法", "MD5/SHA1/DES/RC4/ECB 已被证明不安全，无法满足现代合规要求。",
         "密码用 bcrypt/argon2；摘要用 SHA-256 及以上；对称加密用 AES-GCM。"),
        (r"pickle\.loads?\s*\(|yaml\.load\s*\((?!.*SafeLoader)|marshal\.loads?",
         "不安全反序列化", "pickle/yaml.load(非SafeLoader) 可执行任意代码，反序列化不可信数据风险极高。",
         "用 json 或 yaml.safe_load；确需反序列化时做签名校验。"),
        (r"allow_origins\s*=\s*\[\s*['\"]\*['\"]\s*\][\s\S]{0,40}allow_credentials\s*=\s*True",
         "CORS 通配源允许凭据", "allow_origins=* 同时允许携带凭据，会导致跨站凭据泄露。",
         "明确列出可信来源域名，通配源下禁止 allow_credentials。"),
        (r"(?:log(?:ger)?(?:\.(?:info|error|warning|debug|critical))?|print)\s*\([\s\S]{0,60}(?:password|密码|id_?card|身份证|bank.?card|银行卡|token|secret|api[_-]?key)",
         "日志疑似记录敏感信息", "将密码/身份证/银行卡/token 写入日志，违反数据最小化与脱敏要求。",
         "日志输出前对敏感字段脱敏或直接禁止记录。"),
        (r"chmod\s+(?:777|0o777)|<permission[^>]*>0777",
         "过宽的文件权限", "文件/目录权限设为 777，任意用户可读写执行。",
         "遵循最小权限原则，配置文件用 600、目录用 750。"),
        (r"verify\s*=\s*False|CORS[^;]{0,30}disable|ssl[_-]?verify\s*=\s*False|InsecureRequestWarning",
         "禁用了 TLS 证书校验", "verify=False 会关闭证书验证，易受中间人攻击。",
         "生产环境必须启用证书校验，确需关闭时仅限内网测试。"),
    ]

    PERFORMANCE = [
        (r"SELECT\s+\*", "使用 SELECT *", "查询全部列增加IO与网络开销，不利于覆盖索引。",
         "明确列出所需字段。"),
        (r"(?:同步|sync|blocking)[\s\S]{0,20}(?:调用|请求|io)",
         "疑似同步阻塞IO", "同步阻塞调用在高并发下会耗尽线程资源。",
         "改用异步非阻塞IO或消息队列削峰。"),
        (r"(?:for|while)[\s\S]{0,80}?(?:execute|query|find|select)\s*\(",
         "疑似 N+1 查询", "循环内逐条查询数据库，数据量增长时延迟与连接数急剧上升。",
         "用批量查询/IN 语句/JOIN 或预加载（eager loading）合并。"),
        (r"(?:findAll|find_all|query\.all|\.all\(\))[\s\S]{0,40}(?:未?分页|无分页|without\s+limit)",
         "查询未分页", "全量返回大结果集会造成内存与网络压力。",
         "强制分页（LIMIT/OFFSET 或游标），并设置每页上限。"),
        (r"(?:send_mail|sendmail|requests\.(?:post|get)|http)\s*\([^)]*\)[\s\S]{0,30}(?:同步|等待|await 之外)",
         "请求路径内同步外呼", "在用户请求链路中同步调用邮件/外部HTTP，会放大尾延迟。",
         "改为异步任务/消息队列，外部调用设置超时与熔断。"),
    ]

    def chapter_checks(self, text: str) -> List[ChapterCheck]:
        return [
            ChapterCheck(chapter=name, present=bool(re.search(pat, text, re.IGNORECASE)),
                         note="已包含" if re.search(pat, text, re.IGNORECASE) else "建议补充该章节")
            for name, pat in self.REQUIRED_CHAPTERS
        ]

    def analyze_security(self, text: str) -> List[RiskItem]:
        return self._pattern_risks(text, self.SECURITY, "SEC", RiskLevel.HIGH)

    def analyze_performance(self, text: str) -> List[RiskItem]:
        return self._pattern_risks(text, self.PERFORMANCE, "PERF", RiskLevel.MEDIUM)

    def _pattern_risks(self, text, patterns, prefix, default_level) -> List[RiskItem]:
        risks = []
        idx = 1
        for pattern, issue, expl, sugg in patterns:
            for ctx in _find_context(text, pattern, window=60)[:3]:
                risks.append(RiskItem(
                    id=f"{prefix}-{idx:03d}", category="安全" if prefix == "SEC" else "性能",
                    risk_level=default_level, issue=issue,
                    location=_clause_location(text, text.find(ctx[:20])),
                    explanation=expl, suggestion=sugg, evidence=ctx[:250]))
                idx += 1
        return risks
