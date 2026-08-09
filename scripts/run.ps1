# =====================================================================
# DocGuard AI - Skill 入口 (FIXED NAME: scripts/run.ps1)
#
# 宿主应用 (WorkBuddy / Qoder / TRAE Work) 硬编码调用本文件作为唯一入口。
# 本脚本：选 Python 解释器 -> 确保本地 venv/依赖 -> 路由到契约入口：
#   serve   -> scripts/server.py（长命模型服务，FastAPI @127.0.0.1:8765）
#   其余动作 -> scripts/client.py（短命 CLI，自动拉起并调用本地服务）
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

# 4) 动作路由（符合官方流程：run.ps1 固定入口 -> client.py 短命入口；serve -> server.py 长命模型服务）
switch ($action) {
    'serve'   { & $py scripts/server.py @rest }
    default   { & $py scripts/client.py $action @rest }
}
