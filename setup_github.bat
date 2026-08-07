@echo off
:: ============================================================
:: Strat Scanner -- One-time GitHub setup
:: Double-click this to create the private GitHub repo and push.
:: Requires: git and GitHub CLI (gh) installed and authenticated.
:: ============================================================

cd /d "C:\Users\chris\Tracing"

echo Initializing git repo...
git init

echo Adding files...
git add stock_strategy_scanner.py
git add stock_strategy_symbols.json
git add db_writer.py
git add requirements.txt
git add .gitignore

git commit -m "Initial commit: Strat EOD scanner for CCR deployment"

echo Creating private GitHub repo and pushing...
gh repo create trading --private --source=. --remote=origin --push

echo.
echo Done! Repo URL:
gh repo view --json url -q .url

pause
