# =====================================================================
# DocGuard AI - 独立环境安装脚本（对齐 local-ai-skill-authoring 规范）
#
# 由 scripts/run.ps1 调用，读取 info.json 准备本地 Python venv + 依赖。
# 首次运行创建 .venv 并安装 requirements.txt（国内镜像优先，回退公网 PyPI）。
# =====================================================================
$ErrorActionPreference = 'Stop'
$PSDefaultParameterValues['*:Encoding'] = 'utf8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$SkillDir  = Resolve-Path (Join-Path $ScriptDir '..')

# ---- 日志（对齐官方：%USERPROFILE%\.openvino\log\docguard-install-<ts>.log）----
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
try {
    $logDir = Join-Path $env:USERPROFILE ".openvino\log"
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
} catch {
    $logDir = Join-Path $SkillDir "log"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}
$psLog = Join-Path $logDir "docguard-install-$ts.log"
function Write-DGLog($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [install pid=$PID] $msg"
    Write-Host $line
    Add-Content -Path $psLog -Value $line -Encoding UTF8
}

# 1) 读取 info.json 拿 python_version / venv_name
$infoPath = Join-Path $SkillDir 'info.json'
if (-not (Test-Path $infoPath)) {
    Write-DGLog "ERROR: 未找到 info.json ($infoPath)"
    exit 1
}
$info    = Get-Content $infoPath -Raw | ConvertFrom-Json
$pyVer   = if ($info.python_version) { [string]$info.python_version } else { "3.11" }
$venvName = if ($info.venv_name) { [string]$info.venv_name } else { "docguard" }

# 2) 选择 Python 解释器（用于创建 venv）
$venvPath = Join-Path $SkillDir '.venv'
$venvPy   = Join-Path $venvPath 'Scripts\python.exe'
$py = $null
if (Test-Path $venvPy) {
    $py = $venvPy
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $py = 'python'
} else {
    Write-DGLog "ERROR: 未找到 Python，请先安装 Python $pyVer+ 后再运行 DocGuard。"
    exit 1
}

# 3) 创建虚拟环境（仅首次）
if (-not (Test-Path $venvPy)) {
    Write-DGLog "首次运行：创建虚拟环境 ($venvName, py$pyVer) ..."
    & $py -m venv $venvPath
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

# 4) 安装依赖：国内镜像优先，回退公网 PyPI
$req     = Join-Path $SkillDir 'requirements.txt'
$index   = "https://pypi.tuna.tsinghua.edu.cn/simple"
$trusted = "pypi.tuna.tsinghua.edu.cn"
Write-DGLog "安装依赖 (镜像优先) ..."
& $venvPy -m pip install -r $req -i $index --trusted-host $trusted
if ($LASTEXITCODE -ne 0) {
    Write-DGLog "WARN: 镜像安装失败，回退公网 PyPI ..."
    & $venvPy -m pip install -r $req
    if ($LASTEXITCODE -ne 0) { exit 1 }
}
Write-DGLog "环境就绪 ($venvName)。"
