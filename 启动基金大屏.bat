@echo off
title AI Fund Dashboard

echo ==================================================
echo              AI Fund Dashboard
echo ==================================================
echo.
echo [*] Starting dashboard service...
echo [*] Open this URL after startup: http://localhost:8501
echo.
echo [!] Keep this window open while using the dashboard.
echo ==================================================
echo.

cd /d "%~dp0"
python -m streamlit run web_app.py

if errorlevel 1 (
    echo.
    echo [-] Startup failed. Please run "安装依赖.bat" first.
    echo.
    pause
)
