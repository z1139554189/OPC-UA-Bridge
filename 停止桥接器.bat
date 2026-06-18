@echo off
echo ============================================
echo    OPC UA Bridge - 停止桥接器
echo ============================================
net stop OPCUABridge
if %errorlevel%==0 (
    echo [+] 桥接器: 已停止
) else (
    echo [-] 桥接器: 可能已停止或无法响应
)
pause
