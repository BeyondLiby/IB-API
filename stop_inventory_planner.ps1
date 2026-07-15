[CmdletBinding()]
param(
    [int]$Port = 8766
)

$ErrorActionPreference = "Stop"
$PidFile = Join-Path $env:TEMP "ib_api_inventory_planner_$Port.pid"
$TargetIds = New-Object System.Collections.Generic.HashSet[int]

if (Test-Path $PidFile) {
    $StoredPids = @(Get-Content $PidFile -ErrorAction SilentlyContinue)
    foreach ($StoredPid in $StoredPids) {
        if ($StoredPid -match '^\d+$') {
            [void]$TargetIds.Add([int]$StoredPid)
        }
    }
}

Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { [void]$TargetIds.Add([int]$_.OwningProcess) }

if ($TargetIds.Count -eq 0) {
    Write-Host "No inventory planner server is listening on port $Port."
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    exit 0
}

Write-Host "Stopping inventory planner refresh/server on port ${Port}: $($TargetIds -join ', ')"
$TargetIds | ForEach-Object {
    Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
}
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
Write-Host "Stopped."
