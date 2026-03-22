@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "POLY_APP_ROOT=%~dp0"

if exist "%~dp0PolymarketDesktop_Final\LaunchDesktop.bat" (
    call "%~dp0PolymarketDesktop_Final\LaunchDesktop.bat"
    if errorlevel 1 (
        echo Failed to start packaged Desktop panel.
        pause
        exit /b 1
    )
    exit /b 0
)

where python >nul 2>nul
if errorlevel 1 (
    echo Packaged Desktop panel was not found and Python is not available.
    echo Please keep the PolymarketDesktop_Final folder in this directory.
    pause
    exit /b 1
)

python "%~dp0POLYMARKET_MAKER_copytrade_v2\panel\desktop_launcher.py"
if errorlevel 1 (
    echo Python Desktop panel failed to start.
    pause
    exit /b 1
)
