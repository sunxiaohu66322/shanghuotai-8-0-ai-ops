@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 微信小店·自营上货台 on http://127.0.0.1:9191
start "" http://127.0.0.1:9191
"F:\pyenvs\douyin\Scripts\python.exe" app.py
pause
