# ============================================================
# Strat Bot -- EOD Order Canceller
# Runs Mon-Fri at 21:00 CEST (3:00 PM EDT = US market close -1H)
# Cancels all open/pending Buy Stop orders via paper trading REST API.
# Rationale: Strat combos are bar-specific — once the bar closes the
#            pattern no longer exists. Cancel unfilled orders before
#            EOD so next morning starts clean.
# Requires: TradingView Desktop running with --remote-debugging-port=9222
# ============================================================

$ErrorActionPreference = "Continue"
$TRACING   = "C:\Users\chris\Tracing"
$PYTHON    = "C:\Python314\python.exe"
$LOGDIR    = "$TRACING\bot_logs"
$today     = Get-Date -Format "yyyy-MM-dd"
$logFile   = "$LOGDIR\cancel_run.txt"

if (-not (Test-Path $LOGDIR)) { New-Item -ItemType Directory -Path $LOGDIR | Out-Null }

function Log($msg) {
    $line = "$(Get-Date -Format 'HH:mm:ss') $msg"
    Write-Host $line
    $line | Out-File -Append -Encoding utf8 $logFile
}

Log "=== EOD ORDER CANCEL STARTING ==="
Log "Date: $today  (EOD -1H cancellation)"

# Verify TradingView is running with CDP enabled
try {
    $cdpCheck = Invoke-RestMethod "http://localhost:9222/json" -TimeoutSec 3
    Log "TradingView CDP active ($($cdpCheck.Count) target(s))"
} catch {
    Log "WARNING: TradingView CDP not responding on port 9222"
    Log "Make sure TradingView Desktop is running with --remote-debugging-port=9222"
    Log "=== EOD CANCEL ABORTED ==="
    exit 1
}

# Run the Python canceller
Log "Running tv_cancel_orders.py..."
try {
    $output = & $PYTHON "$TRACING\tv_cancel_orders.py" 2>&1
    $output | ForEach-Object { Log $_ }

    if ($LASTEXITCODE -eq 0) {
        Log "EOD cancellation completed successfully"
    } else {
        Log "Cancellation had errors (exit code $LASTEXITCODE) - check log above"
    }
} catch {
    Log "ERROR running tv_cancel_orders.py: $_"
}

Log "=== EOD ORDER CANCEL DONE ==="
