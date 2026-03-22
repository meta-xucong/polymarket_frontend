@echo off
setlocal
cd /d %~dp0
set POLY_APP_ROOT=%~dp0..\..\
start "" python "%~dp0server.py" --host 127.0.0.1 --port 8787
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8787"
