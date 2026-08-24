---
name: local-docguard
description: |
  企业文档智能审查（DocGuard AI）Agent Skill：在本地 AI PC 上完成企业文档的 OCR + RAG + 文档理解 + 风险分析 + 报告生成。支持合同风险审查、招标文件分析、技术方案审查、企业知识问答（RAG）、文档对比。所有模型与文件均在 localhost（127.0.0.1）运行，企业敏感文档不离开本机，不上传任何云端。底层由 OpenVINO 本地大模型驱动，支持 Intel CPU / GPU / NPU，模型为任意 ≤35B 的 OpenVINO INT4 本地模型（例如 Qwen2.5-7B），由用户自备、Skill 不自动下载。触发词（中英文）：文档审查/合同审查/审合同/招标文件分析/技术方案审查/风险分析/企业知识问答/文档对比/保密审查/合规审查/自动审查/多步审查/投标自检/资质自检；review document / contract review / tender analysis / risk analysis / document Q&A / compare documents / local document review / offline document analysis / autonomous review。当用户给出本地文档路径（.pdf/.docx/.txt/.md/.html）并希望得到结构化风险清单、合同摘要、招标匹配度、技术缺陷或基于文档的问答时优先使用本技能，而非调用云端大模型。支持 Windows + OpenVINO。
---

# DocGuard AI — 企业文档智能审查 Skill

DocGuard AI 是一个**本地优先、支持端云协同**的企业文档智能审查 Agent Skill。它在你的 AI PC 上完成：

- **OCR**：扫描件 / 图片 PDF 文本提取（PaddleOCR，可选）
- **RAG**：文档切片 → 本地 Embedding → FAISS 向量库 → 检索增强问答
- **文档理解**：本地大模型（OpenVINO）做结构化摘要与条款抽取
- **风险分析**：规则引擎 + 大模型双引擎，输出分级风险清单
- **报告生成**：Markdown / HTML / JSON 三态报告
- **端云协同**：默认本地模型；可选 OpenAI 兼容云端模型（如 DashScope / DeepSeek / OpenAI）用于 LLM 增强，**原文件始终不上传**

所有数据、向量与本地模型均运行于 `localhost`；启用云端时，仅向云端发送经脱敏后的文本摘要/检索片段，**原始文件字节永不出机**。

## 架构

```
Agent (Qoder / WorkBuddy / TRAE Work)
        │
        ▼
DocGuard Skill  (tools/*.py — Agent 调用入口)
        │  HTTP (127.0.0.1)
        ▼
DocGuard Local Server  (FastAPI, localhost only)
   ┌────────────┬────────────┬─────────────┬────────────┐
   │ OCR Service│ Document   │ Embedding / │ LLM Reasoning│
   │            │ Parser     │ FAISS Store │ (OpenVINO)   │
   └────────────┴────────────┴─────────────┴────────────┘
        │
        ▼
   DocGuard Analysis Engine → 结构化结果 / 报告
```

## Tools

本 Skill 通过 `tools/` 下六个脚本对外暴露能力，每个脚本输出**标准 JSON**，可直接被 Agent 解析。

### 1. analyze_document

输入：`file_path`（本地文档绝对路径，支持 pdf/docx/txt/md/html）

输出：`document_analysis`（结构化审查结果）

```bash
python tools/analyze_document.py --file "C:/docs/contract.pdf"
python tools/analyze_document.py --file "tender.docx" --type tender
python tools/analyze_document.py --file "design.docx" --no-llm   # 仅规则引擎
python tools/analyze_document.py --file "contract.pdf" --cloud   # 使用云端 LLM 增强（需配置）
```

返回字段（节选）：

- `document_id` / `file_name` / `page_count` / `chunk_count`
- `summary`：标题、文档类型、相关方、关键要点
- `risks`：`[{id, category, risk_level(Low|Medium|High), issue, location, explanation, suggestion, evidence}]`
- `overall_risk_level` + `risk_count_by_level`
- 招标文档另含 `requirements` / `capability_match_score` / `missing_capabilities`
- 技术文档另含 `chapter_checks` / `security_issues` / `performance_risks`

**返回示例（节选）**：

```json
{
  "document_id": "doc_8f3a",
  "file_name": "采购合同.pdf",
  "page_count": 12,
  "overall_risk_level": "High",
  "risk_count_by_level": {"High": 2, "Medium": 5, "Low": 3},
  "summary": {"title": "采购合同", "doc_type": "contract", "parties": ["甲方：X 公司", "乙方：Y 公司"]},
  "risks": [
    {
      "id": "R1",
      "category": "付款条款",
      "risk_level": "High",
      "issue": "付款周期未明确约定，存在资金占用风险",
      "location": "第 4 条 · 第 3 页",
      "explanation": "合同未约定具体付款日期与逾期责任",
      "suggestion": "补充付款节点、金额比例及逾期违约金",
      "evidence": "第四条 价款支付：双方另行协商"
    }
  ],
  "llm_used": true,
  "llm_model_name": "Qwen2.5-7B-Instruct-int4-ov"
}
```

> 注：`llm_used` 为 `true` 表示本次结论有本地大模型参与增强；未准备本地模型时该字段为 `false`，结论仍由规则引擎产出，可独立复核。

### 2. search_document

输入：`query`（自然语言问题），可选 `doc_id`（限定文档）、`top_k`（检索条数）

输出：`retrieved_context`（检索片段 + 基于 RAG 的回答）

```bash
python tools/search_document.py --query "这个合同的付款周期是多少？"
python tools/search_document.py --query "违约条款" --doc-id <id> --top-k 5
python tools/search_document.py --query "违约责任" --cloud   # 云端 LLM 生成回答（需配置）
```

返回字段：`answer`（基于检索内容的回答）、`chunks`（带 `section`/`page`/`score`/`text` 的引用来源）。

### 3. generate_report

输入：`analysis_result`（来自 analyze_document 的 `document_id`，或本地 JSON 文件）

输出：`report_file`（Markdown / HTML / JSON 报告，返回 `download_url`）

```bash
python tools/generate_report.py --doc-id <id> --format html
python tools/generate_report.py --analysis result.json --format markdown
```

### 4. check_bid（招投标资格自检）

输入：招标文件（`--tender` 本地路径，或已分析的 `--doc-id`）+ 我方资质（`--profile-text` 文本和/或 `--profile-file` 本地文件）

输出：`bid_evaluation`（逐条资质核对 + 匹配度 + 能投/不能投结论 + 废标级缺口清单）

```bash
# 招标文件 + 资质文本
python tools/check_bid.py --tender tender.docx --profile-text "注册资本5000万，ISO9001，3个高校项目..."
# 招标文件 + 资质文件
python tools/check_bid.py --tender tender.pdf --profile-file our_company.docx
# 复用已分析的招标书 + 仅规则引擎
python tools/check_bid.py --doc-id <id> --profile-text "..." --no-llm
```

核对逻辑：确定性规则自动判定（注册资本数值比对、类似项目数量、ISO/CMMI/高新等证书、信息系统项目管理师/PMP 等人员证书、从业年限），保证金/交付期/技术规范等"履约动作"标记为待确认而非资质缺口；本地 LLM 可用时对"待确认"项做语义判断升级。返回字段：

- `verdict`：能投/谨慎/不建议投标的结论
- `score`：满足度百分比（待确认项按 0.5 计）
- `items[]`：每条要求的 `status(matched|uncertain|missing)`、`hard_gate`（是否废标级硬门槛）、`reason`、`evidence`
- `blocking_gaps[]`：未满足的硬性资质门槛清单（废标风险）

### 5. compare_documents（文档版本对比）

输入：两版文档（`--old` / `--new`，支持 pdf/docx/txt/md/html）

输出：`comparison`（结构化差异 + 高风险变化提示）

```bash
python tools/compare_documents.py --old contract_v1.docx --new contract_v2.docx
python tools/compare_documents.py -a old.pdf -b new.pdf --type contract
```

返回字段：`segments[]`（`type=added|removed|modified` + 双方文本 + 定位条款）、`change_count`、`summary`（自动提示是否涉及金额、期限、违约责任、主体变化）。适用于合同谈判多版本比对、标书修订追踪。

### 6. agent_run（自主多步编排 · Agentic 模式）

输入：`--goal` 自然语言目标 + 文档（`--file` 可重复传多份）+ 可选 `--question`（RAG 提问）、`--profile`（投标资质）、`--no-llm`（纯确定性管线）

输出：`agent_run`（最终结论 + 完整步骤 trace + 各步 artifacts）

```bash
# 单文档全自动：Agent 自己规划 分析->自检->问答 链
python tools/agent_run.py --file 招标书.docx --profile "注册资本5000万，ISO9001" --goal "判断我方是否应投标"
# 双版本对比 + 提问
python tools/agent_run.py --file v1.docx --file v2.docx --question "新付款条件是什么" --goal "对比重大变化"
```

编排方式：本地 LLM 可用时由模型做 ReAct 式规划（思考->行动->观察循环，决定调用 analyze/bid_check/compare/search 哪个工具、读到中间结果后再定下一步）；模型不可用时回退确定性管线（按文档类型与输入自动串联），始终产出正确结果。返回字段：

- `answer`：最终中文结论
- `planner`：`llm`（模型规划）| `deterministic`（确定性管线）
- `llm_used` / `llm_model_name`：本次是否真实调用了本地 OpenVINO 模型
- `steps[]`：每步的 `thought`（模型思考）、`action`（调用的工具）、`args`、`observation`（观察结果）、`duration_s` —— 完整 Agentic 执行证据链
- `artifacts`：各工具产出的完整结构化结果

同时本 Skill 具备**双向互操作**能力：对外提供 6 个独立 CLI 工具与本地 HTTP API（127.0.0.1:8765），可被任何其他 Skill / Agent 直接调用；对内编排器组合的就是这同一组工具入口。

## 示例（Examples）

| 用户意图 | 调用 |
|---|---|
| "审查这份采购合同" | `analyze_document.py --file 采购合同.pdf` |
| "分析招标文件，看我们能不能投" | `analyze_document.py --file 招标书.docx --type tender` |
| "对照这份招标书，我们公司资质够不够" | `check_bid.py --tender 招标书.docx --profile-file 我司资质.docx` |
| "对比这两版合同改了什么，有没有坑" | `compare_documents.py --old v1.docx --new v2.docx` |
| "审查这份技术方案有没有安全问题" | `analyze_document.py --file 设计方案.docx --type technical` |
| "这个合同付款周期和违约责任是什么？" | `search_document.py --query "付款周期和违约责任"` |
| "出一份 HTML 审查报告" | `generate_report.py --doc-id <id> --format html` |
| "全流程自动审一遍，该查的都查" | `agent_run.py --file 合同.pdf --goal "全面审查"` |

## 何时使用 / 何时不使用

**应使用本 Skill（触发条件）**：
- 用户提供了本地文档路径（`.pdf/.docx/.txt/.md/.html`）并希望得到结构化风险清单、合同摘要、招标匹配度、技术缺陷，或基于文档的问答。
- 任务属于合同风险审查、招标文件分析、技术方案审查、企业知识问答（RAG）、文档对比、保密 / 合规审查。
- 用户强调数据不出机、离线可用、隐私优先。

**不应使用本 Skill（边界）**：
- 文档仍在远端网盘 / 邮件中尚未落地到本地 —— 请先把文件下载到本机再调用。
- 需要实时联网数据（行情、实时新闻、实时法规库）—— 本 Skill 不联网。
- 用户明确要求使用特定云端超大模型做超长上下文推理 —— 本 Skill 仅加载本地 ≤35B 模型；云端为可选增强且默认关闭。
- 非文档类任务（代码生成、纯闲聊、通用知识问答）—— 本 Skill 聚焦「文档内容理解」。

## 输出解读

- `analyze_document` 终端会打印完整 `document_analysis` JSON；高风险项标 `High`，并给出 `location`（条款/页码）与 `suggestion`（修改建议）。
- `search_document` 先检索相关片段再生成回答，每条回答附带引用来源（`[1] 第 N 页 · 相似度 xx%`）。
- `generate_report` 返回 `download_url`，浏览器或 Agent 可直接打开。

## 失败处理

- **服务未启动**：脚本会自动拉起本地 Server（首次约 2–5 秒）；可用 `--no-auto-start` 关闭自动拉起。
- **缺少可选依赖（OCR/Embedding 模型）**：自动降级为「规则引擎模式」，仍输出真实风险分析，不会返回空。
- **大模型未加载**：自动回退到规则引擎 + 关键词抽取，保证审查结论始终可用。
- **文件格式不支持**：返回明确错误，支持 pdf/docx/txt/md/html。
- **首次未准备模型**：不会触发下载；若需 LLM 增强请先按 `info.json` 放置本地 OpenVINO 模型，否则自动走规则引擎降级。

## Important

- **宿主调用入口**：WorkBuddy / Qoder / TRAE 等宿主通过 `scripts/run.ps1` 统一启动与调用本 Skill（详见 `info.json`），支持动作 `analyze / search / report / bid / compare / agent`（对应 `scripts/client.py` 路由到 `tools/*.py` 六个脚本）；Agent 也可直接执行 `tools/*.py`。
- **不要直接调用** `server/` 内部模块，统一经由 `tools/*.py` 入口。
- **本地优先**：默认不依赖任何云服务，断网可用；文件、向量、本地模型均在 `localhost`。
- **不支持平台**：宿主入口为 PowerShell `scripts/run.ps1`，**仅支持 Windows**（Linux / macOS 需自行提供等价启动脚本）。内存建议 ≥ `info.json` 中 `mem_need_gb`（默认 8GB）。
- **纯本地锁死（赛事硬要求）**：默认 `security.local_only=true`，云端模型仅在用户于 `model_config.yaml` 显式开启且自备 API key 时方可启用，且**原始文档字节永不出机**（仅发送脱敏文本片段）。
- **模型用户自备、不自动下载**：本 Skill 不会自动下载任何模型权重。首次运行请按 `info.json` 准备本地 OpenVINO 模型（任意 ≤35B INT4，如 Qwen2.5-7B）与可选 embedding 模型；无模型时自动降级为「规则引擎 + 哈希嵌入」，审查结论仍真实可用。
- **端云协同（可选）**：在 `model_config.yaml` 中开启 `providers.cloud.enabled=true`、配置 endpoint/model，并通过环境变量设置 API key 后，用户可通过 `--cloud` 参数（由 Agent 在调用 `tools/*.py` 时传入）启用云端 LLM。**原始文件字节永远不会上传**，仅上传文本摘要/检索片段。
- **安全开关**：`security.local_only=true` 时，无论 Agent 或任何调用方如何请求，云端 LLM 都会被强制拒绝，确保严格本地合规。
- **本地模型需自行准备**：Skill 不会自动下载任何模型。未准备本地模型时，自动降级为「规则引擎模式」（仍可输出真实风险分析）；如需本地大模型增强，请将任意 ≤35B 的 OpenVINO INT4 模型放到本地目录（参见 `model_config.yaml` 与 `scripts/convert_model.py`），并在 `model_config.yaml` 的 `providers.local.python` 填入装有 `openvino-genai` 的 Python 绝对路径（或用环境变量 `DOCGUARD_OPENVINO_PYTHON` 覆盖）。
- **平台提示**：面向 Windows + OpenVINO 优化（GPU/NPU 优先，CPU 回退）；非 Windows 平台仅 CPU 回退。
- **隐私**：日志已对手机号、身份证、邮箱、银行卡号脱敏；用户文件按 `user_id` 隔离。
- 仅对您**自有且可信**的本地文档使用本能力。
- **环境要求**：需 Python 3.9+（FastAPI / uvicorn 依赖）；依赖隔离安装于 venv（见 `info.json` 与 `requirements.txt`）。本地大模型增强为可选能力，需另行安装 `openvino` / `openvino-genai` 并自备 ≤35B 的 OpenVINO INT4 模型；未安装则自动降级为规则引擎模式，仍可输出真实风险分析。
