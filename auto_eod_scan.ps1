# ============================================================
# Strat Bot — EOD Scanner
# Runs Mon-Fri at 22:30 CEST (30 min after US market close)
# Downloads last 5 daily bars via yfinance, finds Strat setups
# Output: C:\Users\chris\Tracing\stock_strategy_log_YYYY-MM-DD.csv
# ============================================================

$ErrorActionPreference = "Continue"
$TRACING = "C:\Users\chris\Tracing"
$PYTHON   = "C:\Python314\python.exe"
$LOGDIR   = "$TRACING\bot_logs"
$today    = Get-Date -Format "yyyy-MM-dd"
$logFile  = "$LOGDIR\eod_scan_$today.txt"

# Ensure log directory exists
if (-not (Test-Path $LOGDIR)) { New-Item -ItemType Directory -Path $LOGDIR | Out-Null }

function Log($msg) {
    $line = "$(Get-Date -Format 'HH:mm:ss') $msg"
    Write-Host $line
    $line | Out-File -Append -Encoding utf8 $logFile
}

Log "=== EOD SCANNER STARTING ==="
Log "Date: $today"

# Run the scanner
try {
    $result = & $PYTHON "$TRACING\stock_strategy_scanner.py" 2>&1
    $result | ForEach-Object { Log $_ }

    $outFile = "$TRACING\stock_strategy_log_$today.csv"
    if (Test-Path $outFile) {
        $rowCount = (Import-Csv $outFile).Count
        Log "SUCCESS: $rowCount setups written to $outFile"
    } else {
        Log "WARNING: Output file not found — no setups today or scan failed"
    }
} catch {
    Log "ERROR: $_"
}

Log "=== EOD SCANNER DONE ==="
