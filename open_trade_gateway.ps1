[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("paper", "live")]
    [string]$Mode = "paper",

    [string]$Account = $env:IB_ACCOUNT,
    [string]$IbHost = $env:IB_HOST,
    [Nullable[int]]$IbPort,
    [Nullable[int]]$ClientId,
    [Nullable[int]]$MaxOrderQuantity,
    [Nullable[int]]$MaxPreviewQuantity,
    [Nullable[double]]$MinimumReserveFunds,
    [int]$GatewayPort = 8767,
    [string]$PythonPath = $env:PLANNER_PYTHON
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$GatewayScript = Join-Path $ProjectRoot "open_trade_gateway.py"

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
        & $Candidate -c "import ib_async" *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

if ([string]::IsNullOrWhiteSpace($Account)) {
    throw "Set IB_ACCOUNT or pass -Account explicitly before starting the trade gateway."
}
$Account = $Account.Trim()
if ([string]::IsNullOrWhiteSpace($IbHost)) {
    $IbHost = "127.0.0.1"
}
if ($GatewayPort -lt 1 -or $GatewayPort -gt 65535) {
    throw "GatewayPort must be between 1 and 65535."
}

$DefaultIbPort = if ($Mode -eq "paper") { 4002 } else { 4001 }
$IbPort = Resolve-IntegerSetting $IbPort "IB_PORT" $DefaultIbPort
$ClientId = Resolve-IntegerSetting $ClientId "IB_TRADE_CLIENT_ID" 7321
$MaxOrderQuantity = Resolve-IntegerSetting $MaxOrderQuantity "IB_MAX_ORDER_QUANTITY" 10
$MaxPreviewQuantity = Resolve-IntegerSetting $MaxPreviewQuantity "IB_MAX_PREVIEW_QUANTITY" 100
$MinimumReserveFunds = Resolve-DoubleSetting $MinimumReserveFunds "IB_MINIMUM_RESERVE_FUNDS" 1000.0

if ($IbPort -lt 1 -or $IbPort -gt 65535) {
    throw "IB port must be between 1 and 65535."
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
    throw "Cannot find a Python environment with ib_async. Set PLANNER_PYTHON or pass -PythonPath."
}

$ExpectedConfirmation = "{0} {1}" -f $Mode.ToUpperInvariant(), $Account
Write-Host "Mode: $($Mode.ToUpperInvariant())"
Write-Host "IB endpoint: ${IbHost}:$IbPort"
Write-Host "Local trade gateway: http://127.0.0.1:$GatewayPort"
Write-Host "Max real order quantity: $MaxOrderQuantity"
Write-Host "Minimum post-trade reserve: $MinimumReserveFunds"
$Confirmation = Read-Host "Type exactly '$ExpectedConfirmation' to start the order-capable process"
if ($Confirmation -cne $ExpectedConfirmation) {
    Write-Host "Confirmation did not match. Trade gateway was not started."
    exit 1
}

$GatewayArguments = @(
    $GatewayScript,
    "--mode", $Mode,
    "--host", "127.0.0.1",
    "--port", $GatewayPort,
    "--ib-host", $IbHost,
    "--ib-port", $IbPort,
    "--client-id", $ClientId,
    "--account", $Account,
    "--max-order-quantity", $MaxOrderQuantity,
    "--max-preview-quantity", $MaxPreviewQuantity,
    "--minimum-reserve-funds", $MinimumReserveFunds,
    "--enable-order-transmission"
)
if ($Mode -eq "live") {
    $GatewayArguments += @("--live-account-confirm", $Account)
}

Write-Host "Starting the trade gateway in the foreground. Press Ctrl+C to stop it."
Push-Location $ProjectRoot
try {
    & $Python @GatewayArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Trade gateway exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
