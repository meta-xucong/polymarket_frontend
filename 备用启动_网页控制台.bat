@echo off
setlocal
cd /d %~dp0
set POLY_APP_ROOT=%~dp0
if exist "%~dp0PolymarketDesktop_Final\LaunchWebPanel.bat" (
    start "" "%~dp0PolymarketDesktop_Final\LaunchWebPanel.bat"
    exit /b 0
)
where python >nul 2>nul
if errorlevel 1 (
    echo 未找到可运行的发布版，也未检测到 Python。
    echo 请使用 PolymarketDesktop_Final 中的发布包，或先在本机安装 Python。
    pause
    exit /b 1
)
start "" python "%~dp0POLYMARKET_MAKER_copytrade_v2\panel\server.py" --host 127.0.0.1 --port 8787
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8787"
