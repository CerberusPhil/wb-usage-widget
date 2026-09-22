@echo off
rem Rebuild WBCreditWidget.exe from source (run inside the packaging\ folder)
setlocal
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.8+ first.
  pause
  exit /b 1
)
python -m pip show pyinstaller >nul 2>nul
if errorlevel 1 python -m pip install pyinstaller
python -m PyInstaller WBCreditWidget.spec --noconfirm
if errorlevel 1 (
  echo [ERROR] build failed. See messages above.
  pause
  exit /b 1
)
echo Done: dist\WBCreditWidget.exe
pause
