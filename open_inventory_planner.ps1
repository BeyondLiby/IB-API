[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [int]$Port = 8766,
    [Nullable[double]]$RefreshMinutes,
    [Nullable[int]]$ClientId,
    [string]$PythonPath = $env:PLANNER_PYTHON,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RefreshScript = Join-Path $ProjectRoot "refresh_inventory_data.py"
$HostAddress = "127.0.0.1"
$Url = "http://${HostAddress}:$Port/sell_side_inventory_planner.html"
$HealthUrl = "http://${HostAddress}:$Port/inventory-planner-defaults.json"
$Log = Join-Path $env:TEMP "ib_api_inventory_planner_$Port.log"
$ErrorLog = Join-Path $env:TEMP "ib_api_inventory_planner_$Port.error.log"
$PidFile = Join-Path $env:TEMP "ib_api_inventory_planner_$Port.pid"

function Resolve-IntegerSetting {
    param(
        [Nullable[int]]$ExplicitValue,
        [string]$EnvironmentName,
        [int]$DefaultValue
    )

    if ($null -ne $ExplicitValue) {
        return [int]$ExplicitValue
    }
    $RawValue = [Environment]::GetEnvironmentVariable($EnvironmentName)
    if ([string]::IsNullOrWhiteSpace($RawValue)) {
        return $DefaultValue
    }
    $ParsedValue = 0
    if (-not [int]::TryParse($RawValue, [ref]$ParsedValue)) {
        throw "$EnvironmentName must be an integer; received '$RawValue'."
    }
    return $ParsedValue
}

function Resolve-DoubleSetting {
    param(
        [Nullable[double]]$ExplicitValue,
        [string]$EnvironmentName,
        [double]$DefaultValue
    )

    if ($null -ne $ExplicitValue) {
        return [double]$ExplicitValue
    }
    $RawValue = [Environment]::GetEnvironmentVariable($EnvironmentName)
    if ([string]::IsNullOrWhiteSpace($RawValue)) {
        return $DefaultValue
    }
    $ParsedValue = 0.0
    if (-not [double]::TryParse($RawValue, [ref]$ParsedValue)) {
        throw "$EnvironmentName must be numeric; received '$RawValue'."
    }
    return $ParsedValue
}

function Test-PlannerPython {
    param([string]$Candidate)

    if ([string]::IsNullOrWhiteSpace($Candidate) -or -not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        return $false
    }
    try {
        & $Candidate -c "import pandas, ib_async" *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Test-PlannerReady {
    try {
        $Response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 1
        return $Response.StatusCode -eq 200 `
            -and $Response.Content -match '"products"' `
            -and $Response.Content -match '"defaults"'
    }
    catch {
        return $false
    }
}

if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535."
}
$RefreshMinutes = Resolve-DoubleSetting $RefreshMinutes "REFRESH_MINUTES" 1.0
$ClientId = Resolve-IntegerSetting $ClientId "IB_CLIENT_ID" 7316
if ($RefreshMinutes -le 0) {
    throw "RefreshMinutes must be greater than zero."
}
if ($ClientId -lt 0) {
    throw "ClientId must not be negative."
}

$Existing = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($Existing.Count -gt 0) {
    if (Test-PlannerReady) {
        Write-Host "Inventory planner is already running: $Url"
        if (-not $NoBrowser) {
            Start-Process $Url
        }
        exit 0
    }
    throw "Port $Port is already in use by another process. Run .\stop_inventory_planner.ps1 -Port $Port or choose another port."
}

$PythonCandidates = @()
if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonCandidates += $PythonPath
}
if (-not [string]::IsNullOrWhiteSpace($env:CONDA_PREFIX)) {
    $PythonCandidates += (Join-Path $env:CONDA_PREFIX "python.exe")
}
$PythonCandidates += (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
$PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if ($null -ne $PythonCommand) {
    $PythonCandidates += $PythonCommand.Source
}

$Python = $null
foreach ($Candidate in ($PythonCandidates | Select-Object -Unique)) {
    if (Test-PlannerPython $Candidate) {
        $Python = (Resolve-Path -LiteralPath $Candidate).Path
        break
    }
}
if ($null -eq $Python) {
    throw "Cannot find a Python environment with pandas and ib_async. Set PLANNER_PYTHON or pass -PythonPath."
}

$PlannerArguments = @(
    "-u",
    ('"{0}"' -f $RefreshScript),
    "--refresh-mode", "scheduled",
    "--repeat-minutes", $RefreshMinutes,
    "--serve-planner",
    "--planner-host", $HostAddress,
    "--planner-port", $Port,
    "--client-id", $ClientId
) -join " "

Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
Write-Host "Starting background planner with US/Eastern date-aware refresh every $RefreshMinutes minute(s)..."
Write-Host "Log: $Log"
$PlannerProcess = Start-Process -FilePath $Python -ArgumentList $PlannerArguments -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $Log -RedirectStandardError $ErrorLog -WindowStyle Hidden -PassThru
$PlannerProcess.Id | Set-Content -Path $PidFile -Encoding ascii

for ($Attempt = 0; $Attempt -lt 50; $Attempt++) {
    Start-Sleep -Milliseconds 200
    if (Test-PlannerReady) {
        Write-Host "Open: $Url"
        if (-not $NoBrowser) {
            Start-Process $Url
        }
        exit 0
    }
    if ($PlannerProcess.HasExited) {
        break
    }
}

Write-Host "Planner did not start cleanly. Last log lines:"
if (Test-Path $Log) { Get-Content $Log -Tail 40 }
if (Test-Path $ErrorLog) { Get-Content $ErrorLog -Tail 40 }
if (-not $PlannerProcess.HasExited) {
    Stop-Process -Id $PlannerProcess.Id -Force -ErrorAction SilentlyContinue
}
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
throw "Planner server did not become ready on port $Port."
