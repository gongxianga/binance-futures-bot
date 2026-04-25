@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting Binance Futures Bot...
echo Browser will open automatically at http://localhost:5000
python app.py
if %errorlevel% neq 0 pause
