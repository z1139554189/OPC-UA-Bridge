@echo off
chcp 65001 >nul
echo ============================================
echo    OPC UA Bridge 停止脚本
echo ============================================
echo.

echo [1/2] 停止桥接器服务...
net stop OPCUABridge
if %errorlevel%==0 (
    echo [+] 桥接器: 已停止
) else (
    echo [-] 桥接器: 可能已停止或无法响应
)

echo [2/2] 停止调度器服务...
net stop OPCUAScheduler
if %errorlevel%==0 (
    echo [+] 调度器: 已停止
) else (
    echo [-] 调度器: 可能已停止或无法响应
)

echo.
echo 当前状态:
powershell -Command "Get-Service OPCUABridge,OPCUAScheduler | Select Name,Status | Format-Table -AutoSize"

echo.
echo ============================================
echo    服务已停止
echo ============================================
pause
