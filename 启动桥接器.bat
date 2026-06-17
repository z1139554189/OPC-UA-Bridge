@echo off
chcp 65001 >nul
echo ============================================
echo    OPC UA Bridge 启动脚本
echo ============================================
echo.

echo [1/3] 启动桥接器服务...
net start OPCUABridge
if %errorlevel%==0 (
    echo [+] 桥接器: 已启动
) else (
    echo [-] 桥接器: 可能已在运行
)

echo [2/3] 启动调度器服务...
net start OPCUAScheduler
if %errorlevel%==0 (
    echo [+] 调度器: 已启动
) else (
    echo [-] 调度器: 可能已在运行
)

echo.
echo [3/3] 验证服务状态...
powershell -Command "Get-Service OPCUABridge,OPCUAScheduler | Select Name,Status | Format-Table -AutoSize"

echo.
echo ============================================
echo    Dashboard: http://localhost:8000/dashboard
echo ============================================
pause
