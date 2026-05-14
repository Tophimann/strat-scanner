# ============================================================
# Strat Bot — Morning Executor
# Runs Mon-Fri at 15:15 CEST (15 min before US market open)
# Reads latest scan CSV, checks which setups triggered via live
# prices from yfinance, writes trades CSV for Claude to action
# Output: C:\Users\chris\Tracing\stock_strategy_trades_YYYY-MM-DD.csv
# ============================================================

$ErrorActionPreference = "Continue"
$TRACING = "C:\Users\chris\Tracing"
$PYTHON   = "C:\Python314\python.exe"
$LOGDIR   = "$TRACING\bot_logs"
$today    = Get-Date -Format "yyyy-MM-dd"
$logFile  = "$LOGDIR\morning_exec_$today.txt"

if (-not (Test-Path $LOGDIR)) { New-Item -ItemType Directory -Path $LOGDIR | Out-Null }

function Log($msg) {
    $line = "$(Get-Date -Format 'HH:mm:ss') $msg"
    Write-Host $line
    $line | Out-File -Append -Encoding utf8 $logFile
}

Log "=== MORNING EXECUTOR STARTING ==="
Log "Date: $today"

# Find latest scan log (may be from previous day on Mondays)
$latestLog = Get-ChildItem "$TRACING\stock_strategy_log_*.csv" |
             Sort-Object Name | Select-Object -Last 1

if (-not $latestLog) {
    Log "ERROR: No scan log found. Run EOD scanner first."
    exit 1
}
Log "Using scan file: $($latestLog.Name)"

try {
    $result = & $PYTHON "$TRACING\stock_strategy_executor.py" 2>&1
    $result | ForEach-Object { Log $_ }

    $tradesFile = "$TRACING\stock_strategy_trades_$today.csv"
    if (Test-Path $tradesFile) {
        $trades = Import-Csv $tradesFile
        Log "SUCCESS: $($trades.Count) trades written to $tradesFile"
        Log "Trades:"
        $trades | ForEach-Object {
            Log "  BUY $($_.symbol) x$($_.shares) @ $($_.fill_price) | Stop $($_.stop) | Target $($_.target)"
        }
    } else {
        Log "No trades triggered today."
    }
} catch {
    Log "ERROR: $_"
}

Log "=== MORNING EXECUTOR DONE ==="
