@echo off
rem ============================================================
rem Stop WorkBuddy billing widget + its background service (8790)
rem ------------------------------------------------------------
rem Safe to run anytime: it only kills processes whose command
rem line contains billing_server.py or billing_widget.py.
rem Use this when you want to fully quit (closing the widget
rem window with the X button leaves the sync service running).
rem ============================================================
setlocal
echo Stopping WB billing widget / service ...

powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'billing_(server|widget)\.py' } | ForEach-Object { Write-Host ('  kill PID ' + $_.ProcessId + '  (' + $_.Name + ')'); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo Done. (port 8790 released)
pause
