@echo off
setlocal
cd /d %~dp0
set POLY_APP_ROOT=%~dp0app_root
set POLY_DESKTOP_BIN_DIR=%~dp0bin
set POLY_DESKTOP_FORCE_BROWSER=1
set POLY_FORCE_SOURCE_SERVICES=1
start "" "%~dp0PolymarketWebPanel.exe"
