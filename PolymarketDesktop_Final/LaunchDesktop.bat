@echo off
setlocal
cd /d %~dp0
set POLY_APP_ROOT=%~dp0app_root
set POLY_DESKTOP_BIN_DIR=%~dp0bin
set POLY_DESKTOP_APP_MODE=desktop
start "" "%~dp0PolymarketDesktop.exe"
