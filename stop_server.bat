@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "EXE="
if exist "release\库存管理服务器.exe" set "EXE=release\库存管理服务器.exe"
if not defined EXE if exist "库存管理服务器.exe" set "EXE=库存管理服务器.exe"
if not defined EXE if exist "StoreInventoryServer.exe" set "EXE=StoreInventoryServer.exe"
if not defined EXE if exist "release\StoreInventoryServer.exe" set "EXE=release\StoreInventoryServer.exe"
if not defined EXE (
  echo 找不到当前安装的服务器程序。
  pause
  exit /b 1
)
start "" /wait "%EXE%" --stop-local
if errorlevel 1 (
  echo 未确认当前服务器已停止。请查看服务器窗口或日志。
  pause
  exit /b 2
)
echo 本版受管实例已停止或尚未运行。开机自启设置不变。
pause
