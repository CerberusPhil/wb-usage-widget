@echo off
rem WorkBuddy Billing Dashboard - sync billing from server, rebuild HTML, open browser
rem Portable: paths relative to this file (%~dp0), Python auto-detected.
rem Override Python by setting env var:  WB_PY=...\python.exe
rem Sync failure (e.g. desktop login expired) degrades gracefully: renders with existing cache.
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

echo [1/2] Syncing billing data from server (www.workbuddy.cn)...
%PY% "%~dp0server_usage_sync.py"
if errorlevel 1 (
  echo [WARN] Sync failed - login state may have expired. Rendering with existing cache.
  echo        Fix: open & re-login the WorkBuddy desktop app (self-heals automatically).
  echo        Fallback: F12-copy cookie from www.workbuddy.cn/profile/plans-usage
  echo        into ~/.workbuddy/server_usage.json
)

echo [2/2] Rebuilding dashboard HTML...
%PY% "%~dp0billing_dashboard_data.py"
if errorlevel 1 (
  echo [ERROR] Rebuild failed. See message above.
  pause
  exit /b 1
)
start "" "%~dp0..\billing_dashboard.html"
endlocal
