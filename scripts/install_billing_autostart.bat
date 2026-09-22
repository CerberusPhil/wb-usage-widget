@echo off
rem Install / remove WorkBuddy billing widget auto-start (Windows Task Scheduler)
rem Usage:
rem   install_billing_autostart.bat          -> register service + widget at logon
rem   install_billing_autostart.bat remove   -> delete the registered tasks
rem All ASCII on purpose (Windows batch rule).
setlocal

set "DIR=%~dp0"
set "SRV=%DIR%billing_server.py"
set "WGT=%DIR%billing_widget.py"
set "TN1=WB Billing Widget Service"
set "TN2=WB Billing Widget"

if "%1"=="remove" goto remove

set "PYWEXE="
if defined WB_PYW set "PYWEXE=%WB_PYW%"
if not defined PYWEXE (
  for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PYWEXE set "PYWEXE=%%i"
)
if not defined PYWEXE (
  for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYWEXE set "PYWEXE=%%i"
)
if not defined PYWEXE (
  echo [ERROR] Python not found.
  echo Install Python 3.8+ or set WB_PYW env var.
  echo Example:  setx WB_PYW "C:\Python313\pythonw.exe"
  pause
  exit /b 1
)

echo Python: %PYWEXE%
echo.
echo Registering "%TN1%" (service, 30s delay after logon)...
schtasks /create /tn "%TN1%" /tr "\"%PYWEXE%\" \"%SRV%\"" /sc onlogon /delay 0000:30 /f
if %errorlevel% neq 0 (
  echo [ERROR] failed to create task "%TN1%". Try running as administrator.
  pause
  exit /b 1
)

echo.
echo Registering "%TN2%" (widget window, 60s delay after logon)...
schtasks /create /tn "%TN2%" /tr "\"%PYWEXE%\" \"%WGT%\"" /sc onlogon /delay 0001:00 /f

echo.
echo Done. Service + widget will start automatically at next logon.
echo   Verify:  schtasks /query /tn "%TN1%"
echo   Remove:  install_billing_autostart.bat remove
echo.
echo Tip: needs pywebview for a true frameless widget:  python -m pip install pywebview
pause
exit /b 0

:remove
echo Removing tasks...
schtasks /delete /tn "%TN1%" /f
schtasks /delete /tn "%TN2%" /f
echo Done.
pause
exit /b 0
