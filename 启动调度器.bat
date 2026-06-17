@echo off
chcp 65001 >nul
echo ============================================
echo    OPC UA Bridge - 启动调度器
echo ============================================
net start OPCUAScheduler
if %errorlevel%==0 (
    echo [+] 调度器: 已启动
) else (
    echo [-] 调度器: 可能已在运行
)
pause
