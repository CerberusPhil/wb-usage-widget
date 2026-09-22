@echo off
rem WorkBuddy billing desktop widget - start local service (8790) then the widget window
rem Portable: paths relative to this file (%~dp0), Python auto-detected.
rem Override Python by setting env vars:  WB_PY / WB_PYW
rem Safe to double-click repeatedly: service exits silently if port 8790 already in use.
setlocal

set "PY="
if defined WB_PY set "PY=%WB_PY%"
if not defined PY (
  where py >nul 2>nul
  if %errorlevel%==0 set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul
  if %errorlevel%==0 set "PY=python"
)
if not defined PY (
  echo [ERROR] Python not found. Install Python 3.8+ or set WB_PY env var.
  pause
  exit /b 1
)

set "PYW="
if defined WB_PYW set "PYW=%WB_PYW%"
if not defined PYW (
  where pyw >nul 2>nul
  if %errorlevel%==0 set "PYW=pyw -3"
)
if not defined PYW set "PYW=%PY%"

echo [1/2] starting billing widget service (http://127.0.0.1:8790)...
start "" %PYW% "%~dp0billing_server.py"
timeout /t 2 /nobreak >nul

echo [2/2] launching desktop widget...
start "" %PYW% "%~dp0billing_widget.py"
endlocal
