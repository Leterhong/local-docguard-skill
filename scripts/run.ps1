# =====================================================================
# DocGuard AI - Skill 入口 (FIXED NAME: scripts/run.ps1)
#
# 宿主应用 (WorkBuddy / Qoder / TRAE Work) 硬编码调用本文件作为唯一入口。
# 流程：确保本地环境(独立 install-env.ps1) -> 路由：
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

# PowerShell 客户端日志（对齐官方：%USERPROFILE%\.openvino\log\docguard-client-<ts>.log）
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
try {
    $logDir = Join-Path $env:USERPROFILE ".openvino\log"
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
} catch {
    $logDir = Join-Path $SkillDir "log"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}
$psLog = Join-Path $logDir "docguard-client-$ts.log"
function Write-DGLog($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [client pid=$PID] $msg"
    Write-Host $line
    Add-Content -Path $psLog -Value $line -Encoding UTF8
}

# 1) 解析动作 + 透传参数
$action = if ($args.Length -ge 1) { $args[0] } else { $null }
$rest   = @()
if ($args.Length -gt 1) { $rest = $args[1..($args.Length - 1)] }

# 2) 确保本地环境就绪（独立 install-env.ps1：建 venv + 装依赖，仅首次）
Write-DGLog "ensure env via scripts/install-env.ps1"
# 兼容 PowerShell 5.1 与 PowerShell 7：优先用当前宿主解释器，避免 pwsh 下 $PSHOME 无 powershell.exe
$hostExe = $null
try { $hostExe = (Get-Process -Id $PID).Path } catch { $hostExe = $null }
if (-not $hostExe -or -not (Test-Path $hostExe)) { $hostExe = Join-Path $PSHOME 'powershell.exe' }
& $hostExe -NoProfile -File (Join-Path $ScriptDir 'install-env.ps1')
if ($LASTEXITCODE -ne 0) {
    Write-DGLog "install-env failed (exit=$LASTEXITCODE)"
    exit 1
}

# 3) 选择 venv python
$venvPy = Join-Path $SkillDir '.venv\Scripts\python.exe'
if (Test-Path $venvPy) { $py = $venvPy } else { $py = 'python' }

# 4) 动作路由（run.ps1 固定入口 -> client.py 短命；serve -> server.py 长命）
switch ($action) {
    'serve'   { Write-DGLog "route -> server.py"; & $py scripts/server.py @rest }
    default   { Write-DGLog "route -> client.py $action"; & $py scripts/client.py $action @rest }
}

# 5) 传播工具退出码给宿主（技能规范：宿主依赖非零退出码识别失败）
exit $LASTEXITCODE
