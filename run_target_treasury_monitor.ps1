$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$repoRoot;$env:PYTHONPATH" } else { $repoRoot }

streamlit run "$repoRoot\target_treasury_account_monitor\app.py" --server.address 127.0.0.1 --server.port 8502
