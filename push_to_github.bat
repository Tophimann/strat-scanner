@echo off
cd /d "C:\Users\chris\Tracing"
git remote add origin https://github.com/Tophimann/trading.git
git branch -M main
git push -u origin main
echo.
echo Done!
pause
