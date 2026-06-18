@echo off
echo Stopping OPCUABridge...
net stop OPCUABridge
if %errorlevel%==0 (echo [OK] OPCUABridge stopped) else (echo [SKIP] already stopped)
pause
