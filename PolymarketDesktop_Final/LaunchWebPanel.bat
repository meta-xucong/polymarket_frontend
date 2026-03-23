@echo off
setlocal
cd /d %~dp0
set POLY_APP_ROOT=%~dp0app_root
set POLY_DESKTOP_BIN_DIR=%~dp0bin
set POLY_DESKTOP_FORCE_BROWSER=1
set POLY_DESKTOP_APP_MODE=browser
start "" "%~dp0PolymarketWebPanel.exe"
