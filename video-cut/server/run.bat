@echo off
REM Video Cut Flask Server Launcher for Windows
REM This batch file automatically uses Python 3.12 for YouTube video extraction

echo.
echo ==========================================
echo.  Video Cut Flask Server
echo ==========================================
echo.

REM Check if python3.12 is available
where python3.12 >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python 3.12 not found in PATH
    echo.
    echo Please ensure Python 3.12 is installed and added to your PATH
    echo Or run using: python3.12 app.py
    pause
    exit /b 1
)

REM Show Python version
echo Python Version:
python3.12 --version
echo.
echo Server: http://127.0.0.1:5000
echo Press CTRL+C to stop
echo.
echo ==========================================
echo.

REM Start server
cd /d "%~dp0"
python3.12 app.py

pause
