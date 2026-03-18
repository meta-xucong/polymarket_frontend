@echo off
setlocal
cd /d %~dp0
set POLY_APP_ROOT=%~dp0
set POLY_FORCE_SOURCE_SERVICES=1
start "" python "%~dp0POLYMARKET_MAKER_copytrade_v2\panel\desktop_launcher.py"
