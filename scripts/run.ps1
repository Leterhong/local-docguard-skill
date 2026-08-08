# =====================================================================
# DocGuard AI - Skill 入口 (FIXED NAME: scripts/run.ps1)
#
# 宿主应用 (WorkBuddy / Qoder / TRAE Work) 硬编码调用本文件作为唯一入口。
# 本脚本：选 Python 解释器 -> 确保本地 venv/依赖 -> 路由到 tools/ 客户端
# （tools/*.py 会自动拉起本地 FastAPI Server，无需手动起服务）。
# 全部运行于 localhost，文档与模型均不出机。
# =====================================================================
$ErrorActionPreference = 'Stop'
$PSDefaultParameterValues['*:Encoding'] = 'utf8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$SkillDir  = Resolve-Path (Join-Path $ScriptDir '..')
Push-Location $SkillDir

# 1) 解析动作 + 透传参数
$action = if ($args.Length -ge 1) { $args[0] } else { $null }
$rest   = @()
if ($args.Length -gt 1) { $rest = $args[1..($args.Length - 1)] }

# 2) 选择 Python 解释器（优先本地 .venv）
$venvPy = Join-Path $SkillDir '.venv\Scripts\python.exe'
if (Test-Path $venvPy) {
    $py = $venvPy
} else {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $py = 'python'
    } else {
        Write-Error "未找到 Python，请先安装 Python 3.11+ 后再运行 DocGuard。"
        exit 1
    }
}

# 3) 环境就绪：.venv 不存在则创建并安装依赖（仅首次）
if (-not (Test-Path $venvPy)) {
    Write-Host "[DocGuard] 首次运行：创建虚拟环境并安装依赖 (可能需要几分钟)..."
    & $py -m venv .venv
    & .venv\Scripts\python.exe -m pip install -r requirements.txt
}

# 4) 动作路由
switch ($action) {
    'serve'   { & $py -m server.main @rest }
    'analyze' { & $py tools/analyze_document.py @rest }
    'search'  { & $py tools/search_document.py @rest }
    'report'  { & $py tools/generate_report.py @rest }
    default {
        Write-Host "DocGuard AI 用法: run.ps1 <analyze|search|report|serve> [参数]" -ForegroundColor Yellow
        Write-Host "  审查文档 : run.ps1 analyze --file 合同.pdf [--type contract] [--no-llm]"
        Write-Host "  知识问答 : run.ps1 search  --query 付款周期 [--doc-id <id>]"
        Write-Host "  生成报告 : run.ps1 report  --doc-id <id> --format html"
        Write-Host "  启动服务 : run.ps1 serve"
        exit 0
    }
}
