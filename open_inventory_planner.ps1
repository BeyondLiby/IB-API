[CmdletBinding()]
param(
    [int]$Port = 8766,
    [double]$RefreshMinutes = 1,
    [int]$ClientId = 7316
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$RefreshScript = Join-Path $ProjectRoot "refresh_inventory_data.py"
$ServerScript = Join-Path $ProjectRoot "open_inventory_planner.py"
$Url = "http://127.0.0.1:$Port/sell_side_inventory_planner.html"
$ServerLog = Join-Path $env:TEMP "ib_api_inventory_planner_$Port.server.log"
$ServerErrorLog = Join-Path $env:TEMP "ib_api_inventory_planner_$Port.server.error.log"
$RefreshLog = Join-Path $env:TEMP "ib_api_inventory_planner_$Port.refresh.log"
$RefreshErrorLog = Join-Path $env:TEMP "ib_api_inventory_planner_$Port.refresh.error.log"
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

$ServerArguments = @(
    ('"{0}"' -f $ServerScript),
    "--host", "127.0.0.1",
    "--port", $Port,
    "--no-open"
) -join " "

$RefreshArguments = @(
    "-u",
    ('"{0}"' -f $RefreshScript),
    "--refresh-mode", "scheduled",
    "--repeat-minutes", $RefreshMinutes,
    "--client-id", $ClientId
) -join " "

Write-Host "Starting background planner with US/Eastern date-aware refresh every $RefreshMinutes minute(s)..."
Write-Host "Server log: $ServerLog"
Write-Host "Refresh log: $RefreshLog"
$ServerProcess = Start-Process -FilePath $Python -ArgumentList $ServerArguments -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $ServerLog -RedirectStandardError $ServerErrorLog -WindowStyle Hidden -PassThru
$RefreshProcess = Start-Process -FilePath $Python -ArgumentList $RefreshArguments -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $RefreshLog -RedirectStandardError $RefreshErrorLog -WindowStyle Hidden -PassThru
@($ServerProcess.Id, $RefreshProcess.Id) | Set-Content -Path $PidFile -Encoding ascii

for ($Attempt = 0; $Attempt -lt 50; $Attempt++) {
    Start-Sleep -Milliseconds 200
    $Listening = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($Listening.Count -gt 0) {
        Write-Host "Open: $Url"
        Start-Process $Url
        exit 0
    }
}

Write-Host "Planner server did not start cleanly. Last server log lines:"
if (Test-Path $ServerLog) { Get-Content $ServerLog -Tail 40 }
if (Test-Path $ServerErrorLog) { Get-Content $ServerErrorLog -Tail 40 }
Stop-Process -Id $ServerProcess.Id,$RefreshProcess.Id -Force -ErrorAction SilentlyContinue
throw "Planner server did not bind to port $Port."
