@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "POLY_APP_ROOT=%~dp0"

if exist "%~dp0PolymarketDesktop_Final\LaunchWebPanel.bat" (
    call "%~dp0PolymarketDesktop_Final\LaunchWebPanel.bat"
    if errorlevel 1 (
        echo Failed to start packaged Web panel.
        pause
        exit /b 1
    )
    exit /b 0
)

where python >nul 2>nul
if errorlevel 1 (
    echo Packaged Web panel was not found and Python is not available.
    echo Please keep the PolymarketDesktop_Final folder in this directory.
    pause
    exit /b 1
)

python "%~dp0POLYMARKET_MAKER_copytrade_v2\panel\server.py" --host 127.0.0.1 --port 8787
if errorlevel 1 (
    echo Python Web panel failed to start.
    pause
    exit /b 1
)
