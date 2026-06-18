@echo off
echo Starting OPCUAScheduler...
net start OPCUAScheduler
if errorlevel 1 (echo [SKIP] already running) else (echo [OK] OPCUAScheduler started)
pause
