@echo off
chcp 65001 >nul
echo ============================================
echo    OPC UA Bridge - 启动桥接器
echo ============================================
net start OPCUABridge
if %errorlevel%==0 (
    echo [+] 桥接器: 已启动
) else (
    echo [-] 桥接器: 可能已在运行
)
pause
