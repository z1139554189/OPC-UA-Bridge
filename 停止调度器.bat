@echo off
chcp 65001 >nul
echo ============================================
echo    OPC UA Bridge - 停止调度器
echo ============================================
net stop OPCUAScheduler
if %errorlevel%==0 (
    echo [+] 调度器: 已停止
) else (
    echo [-] 调度器: 可能已停止或无法响应
)
pause
