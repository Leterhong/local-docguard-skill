# =====================================================================
# DocGuard AI - E2E test entry (FIXED NAME: tests/test.ps1)
#
# 官方 local-ai-skill-authoring 要求：tests/test.ps1 做真实硬件上的端到端测试。
# 本 Skill 设计为「无模型亦可运行」——以下 pytest 子集仅依赖确定性规则引擎
# 与本地 FastAPI TestClient，无需任何外部模型/权重，适合 CI 与评测机运行。
# =====================================================================
$ErrorActionPreference = 'Stop'
$PSDefaultParameterValues['*:Encoding'] = 'utf8'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$SkillDir  = Resolve-Path (Join-Path $ScriptDir '..')
Push-Location $SkillDir

# 1) 选择 Python 解释器（优先本地 .venv）
$venvPy = Join-Path $SkillDir '.venv\Scripts\python.exe'
if (Test-Path $venvPy) {
    $py = $venvPy
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $py = 'python'
} else {
    Write-Error "未找到 Python，请先安装 Python 3.11+ 后再运行测试。"
    exit 1
}

# 2) 确保依赖（仅首次）
if (-not (Test-Path $venvPy)) {
    & $py -m venv .venv
    & .venv\Scripts\python.exe -m pip install -r requirements.txt
}

# 3) 运行无模型可过的端到端子集
#    test_rules.py : 纯规则引擎确定性测试（合同/招标/技术方案样例）
#    test_api.py   : FastAPI TestClient 集成测试（use_llm=False 降级）
#    test_agent.py : 编排器多步链路测试（确定性规划器，无需模型）
& $py -m pytest tests/test_rules.py tests/test_api.py tests/test_agent.py -q
exit $LASTEXITCODE
