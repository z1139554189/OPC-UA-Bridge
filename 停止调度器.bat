@echo off
echo Stopping OPCUAScheduler...
net stop OPCUAScheduler
if errorlevel 1 (echo [SKIP] already stopped) else (echo [OK] OPCUAScheduler stopped)
pause
