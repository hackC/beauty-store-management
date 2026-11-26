@echo off
chcp 65001 >nul
set "TASK=门店库存管理服务器"
cd /d "%~dp0"

schtasks /Query /TN "%TASK%" >nul 2>nul
if not errorlevel 1 (
  schtasks /Run /TN "%TASK%" >nul 2>nul
  if errorlevel 1 (
    echo 无法启动后台任务，请检查任务计划程序和管理员权限。
    pause
    exit /b 1
  )
  timeout /t 2 /nobreak >nul
  echo 已向 Windows 后台任务发送启动命令。
  echo 请运行 check_server.ps1 查看监听状态和最近错误。
  pause
  exit /b 0
)

if exist "release\库存管理服务器.exe" (
  start "" "release\库存管理服务器.exe" --headless
) else if exist "库存管理服务器.exe" (
  start "" "库存管理服务器.exe" --headless
) else if exist "StoreInventoryServer.exe" (
  start "" "StoreInventoryServer.exe" --headless
) else if exist "release\StoreInventoryServer.exe" (
  start "" "release\StoreInventoryServer.exe" --headless
) else (
  echo 找不到库存管理服务器.exe，请先运行 build_windows.bat 构建。
  pause
  exit /b 1
)
