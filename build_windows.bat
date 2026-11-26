@echo off
setlocal

cd /d "%~dp0"

title Store Inventory Windows Builder

echo ============================================
echo       Store Inventory EXE Builder
echo ============================================
echo.

where powershell.exe >nul 2>nul

if errorlevel 1 (
    echo ERROR: Windows PowerShell was not found.
    pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1"

set "BUILD_EXIT=%ERRORLEVEL%"

echo.

if not "%BUILD_EXIT%"=="0" (
    echo ============================================
    echo BUILD FAILED.
    echo Please read the error message above.
    echo ============================================
) else (
    echo ============================================
    echo BUILD SUCCESSFUL.
    echo ============================================
    echo.
    echo Output:
    echo release\库存管理服务器.exe
)

echo.
pause

exit /b %BUILD_EXIT%