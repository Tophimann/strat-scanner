# ============================================================
# Strat Bot -- Autonomous Order Placement via REST API
# Runs Mon-Fri at 15:30 CEST (US market open 9:30 AM EDT)
# Reads trades CSV, calls TradingView paper trading REST API
# directly using JWT extracted from the running Desktop app.
# Requires: TradingView Desktop running with --remote-debugging-port=9222
#           pip install requests websocket-client
# ============================================================

$ErrorActionPreference = "Continue"
$TRACING   = "C:\Users\chris\Tracing"
$PYTHON    = "C:\Python314\python.exe"
$LOGDIR    = "$TRACING\bot_logs"
$today     = Get-Date -Format "yyyy-MM-dd"
$logFile   = "$LOGDIR\order_placement_$today.txt"
$tradesFile= "$TRACING\stock_strategy_trades_$today.csv"

if (-not (Test-Path $LOGDIR)) { New-Item -ItemType Directory -Path $LOGDIR | Out-Null }

function Log($msg) {
    $line = "$(Get-Date -Format 'HH:mm:ss') $msg"
    Write-Host $line
    $line | Out-File -Append -Encoding utf8 $logFile
}

Log "=== ORDER PLACEMENT STARTING ==="
Log "Date: $today"

# Check if trades file exists
if (-not (Test-Path $tradesFile)) {
    Log "No trades file found at $tradesFile - no orders to place today"
    Log "=== ORDER PLACEMENT DONE (nothing to do) ==="
    exit 0
}

# Read the trades CSV
$trades = Import-Csv $tradesFile
if ($trades.Count -eq 0) {
    Log "Trades file is empty - no orders to place"
    Log "=== ORDER PLACEMENT DONE (nothing to do) ==="
    exit 0
}

Log "Found $($trades.Count) trade(s) to place via REST API"
Log "Trades: $($trades.symbol_tv -join ', ')"

# Verify TradingView is running with CDP enabled
try {
    $cdpCheck = Invoke-RestMethod "http://localhost:9222/json" -TimeoutSec 3
    Log "TradingView CDP active ($($cdpCheck.Count) target(s))"
} catch {
    Log "WARNING: TradingView CDP not responding on port 9222"
    Log "Make sure TradingView Desktop is running with --remote-debugging-port=9222"
    Log "=== ORDER PLACEMENT ABORTED ==="
    exit 1
}

# Run the Python order placer
Log "Running tv_place_orders.py..."
try {
    $output = & $PYTHON "$TRACING\tv_place_orders.py" $today 2>&1
    $output | ForEach-Object { Log $_ }

    # Check exit code
    if ($LASTEXITCODE -eq 0) {
        Log "All orders placed successfully"
    } else {
        Log "Some orders failed (exit code $LASTEXITCODE) - check log above"
    }
} catch {
    Log "ERROR running tv_place_orders.py: $_"
}

Log "=== ORDER PLACEMENT DONE ==="
