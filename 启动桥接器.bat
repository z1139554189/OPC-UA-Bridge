@echo off
echo Starting OPCUABridge...
net start OPCUABridge
if errorlevel 1 (echo [SKIP] OPCUABridge already running) else (echo [OK] OPCUABridge started)
pause
