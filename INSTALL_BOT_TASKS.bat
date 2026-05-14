@echo off
:: =============================================================
:: Strat Bot - Task Scheduler Setup
:: RIGHT-CLICK this file and select "Run as Administrator"
:: =============================================================
:: Schedule (CEST = UTC+2, summer time):
::   Morning Exec: Mon-Fri 15:15  (13:15 UTC = 9:15 AM EDT)
::   Order Place : Mon-Fri 15:30  (13:30 UTC = 9:30 AM EDT)
::   EOD Cancel  : Mon-Fri 21:00  (19:00 UTC = 3:00 PM EDT)
::   EOD Scan    : Mon-Fri 22:30  (20:30 UTC = 4:30 PM EDT)
::
:: DST NOTE: When clocks change, edit /st times:
::   Winter (CET=UTC+1): 14:15 / 14:30 / 20:00 / 21:30
:: =============================================================

set TRACING=C:\Users\chris\Tracing
set PS=C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe

echo.
echo ============================================
echo   Strat Bot - Registering Scheduled Tasks
echo ============================================
echo.

:: Remove old versions if they exist
schtasks /delete /tn "StratBot_EOD_Scan"        /f >nul 2>&1
schtasks /delete /tn "StratBot_Morning_Executor" /f >nul 2>&1
schtasks /delete /tn "StratBot_Place_Orders"     /f >nul 2>&1
schtasks /delete /tn "StratBot_Cancel_Orders"    /f >nul 2>&1

:: Create Task 1: Morning Executor (15:15 Mon-Fri)
schtasks /create ^
  /tn "StratBot_Morning_Executor" ^
  /tr "\"%PS%\" -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File \"%TRACING%\auto_morning_exec.ps1\"" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 15:15 ^
  /f
if %errorlevel%==0 (echo [OK] StratBot_Morning_Executor - Mon-Fri at 15:15) else (echo [FAIL] Morning Executor)

:: Create Task 2: Order Placement (15:30 Mon-Fri)
schtasks /create ^
  /tn "StratBot_Place_Orders" ^
  /tr "\"%PS%\" -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File \"%TRACING%\auto_place_orders.ps1\"" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 15:30 ^
  /f
if %errorlevel%==0 (echo [OK] StratBot_Place_Orders     - Mon-Fri at 15:30) else (echo [FAIL] Place Orders)

:: Create Task 3: EOD Cancel Orders (21:00 Mon-Fri = 3:00 PM EDT)
schtasks /create ^
  /tn "StratBot_Cancel_Orders" ^
  /tr "\"%PS%\" -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File \"%TRACING%\auto_cancel_orders.ps1\"" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 21:00 ^
  /f
if %errorlevel%==0 (echo [OK] StratBot_Cancel_Orders    - Mon-Fri at 21:00) else (echo [FAIL] Cancel Orders)

:: Create Task 4: EOD Scanner (22:30 Mon-Fri)
schtasks /create ^
  /tn "StratBot_EOD_Scan" ^
  /tr "\"%PS%\" -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File \"%TRACING%\auto_eod_scan.ps1\"" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 22:30 ^
  /f
if %errorlevel%==0 (echo [OK] StratBot_EOD_Scan         - Mon-Fri at 22:30) else (echo [FAIL] EOD Scan - try running as Administrator)

echo.
echo ============================================
echo Registered tasks:
schtasks /query /fo TABLE /nh | findstr "StratBot"
echo.
echo Bot logs will appear in: %TRACING%\bot_logs\
echo.
echo REMINDER: Keep TradingView Desktop running for
echo           order placement to work automatically.
echo ============================================
echo.
pause
