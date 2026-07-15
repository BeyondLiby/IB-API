[CmdletBinding()]
param(
    [int]$Port = 8766,
    [double]$RefreshMinutes = 3,
    [int]$ClientId = 7316
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$RefreshScript = Join-Path $ProjectRoot "refresh_inventory_data.py"
$Url = "http://127.0.0.1:$Port/sell_side_inventory_planner.html"
$Log = Join-Path $env:TEMP "ib_api_inventory_planner_$Port.log"
$ErrorLog = Join-Path $env:TEMP "ib_api_inventory_planner_$Port.error.log"
$PidFile = Join-Path $env:TEMP "ib_api_inventory_planner_$Port.pid"

if (-not (Test-Path $Python)) {
    throw "Python environment not found: $Python"
}

$Existing = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($Existing.Count -gt 0) {
    Write-Host "Inventory planner is already running: $Url"
    Start-Process $Url
    exit 0
}

$Arguments = @(
    "-u",
    ('"{0}"' -f $RefreshScript),
    "--refresh-mode", "fast",
    "--repeat-minutes", $RefreshMinutes,
    "--serve-planner",
    "--planner-host", "127.0.0.1",
    "--planner-port", $Port,
    "--client-id", $ClientId
) -join " "

Write-Host "Starting background planner with fast refresh every $RefreshMinutes minutes..."
Write-Host "Log: $Log"
$PlannerProcess = Start-Process -FilePath $Python -ArgumentList $Arguments -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $Log -RedirectStandardError $ErrorLog -WindowStyle Hidden -PassThru
$PlannerProcess.Id | Set-Content -Path $PidFile -Encoding ascii

for ($Attempt = 0; $Attempt -lt 50; $Attempt++) {
    Start-Sleep -Milliseconds 200
    $Listening = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($Listening.Count -gt 0) {
        Write-Host "Open: $Url"
        Start-Process $Url
        exit 0
    }
}

Write-Host "Planner did not start cleanly. Last log lines:"
if (Test-Path $Log) { Get-Content $Log -Tail 40 }
if (Test-Path $ErrorLog) { Get-Content $ErrorLog -Tail 40 }
throw "Planner server did not bind to port $Port."
