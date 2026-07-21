param(
    [ValidateSet("jpm", "gs")]
    [string]$Portal = "jpm",

    [ValidateSet("persistent", "cdp")]
    [string]$Mode = "persistent"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    throw "项目环境不存在，请先运行 .\setup.ps1"
}

& $VenvPython -m portal_crawler snapshot $Portal --mode $Mode

