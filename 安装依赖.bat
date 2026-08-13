@echo off
title Install AI Fund Dashboard Dependencies

cd /d "%~dp0"
echo Installing required Python packages...
python -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo Installation failed. Please install Python 3.10 or later first.
    pause
    exit /b 1
)

echo.
echo Installation complete. Double-click "启动基金大屏.bat" to start.
pause
