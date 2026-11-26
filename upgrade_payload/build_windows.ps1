$ErrorActionPreference = 'Stop'

# ============================================================
# Store Management V1.5.0 DEV - Windows EXE Builder
# Windows PowerShell 5.1
# Python 3.11 / 3.12
# ============================================================

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDir

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Store Management V1.5.0 DEV - Windows EXE Builder" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ------------------------------------------------------------
# Helper: run Python and check exit code
# ------------------------------------------------------------

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$FailureMessage
    )

    $allArgs = @()

    if ($script:PythonPrefix.Count -gt 0) {
        $allArgs += $script:PythonPrefix
    }

    $allArgs += $Arguments

    & $script:PythonCommand @allArgs

    $pythonExitCode = $LASTEXITCODE

    if ($pythonExitCode -ne 0) {
        throw "$FailureMessage Exit code: $pythonExitCode"
    }
}

# ------------------------------------------------------------
# 1. Detect Python 3.11 / 3.12
# ------------------------------------------------------------

$PythonCommand = $null
$PythonPrefix = @()
$PythonVersionText = $null

$pythonCmd = Get-Command python.exe -ErrorAction SilentlyContinue

if ($pythonCmd) {
    try {
        $versionOutput = & $pythonCmd.Source --version 2>&1
        $versionExitCode = $LASTEXITCODE
        $versionText = ($versionOutput | Out-String).Trim()

        if (
            $versionExitCode -eq 0 -and
            $versionText -match '^Python\s+3\.(11|12)\.'
        ) {
            $PythonCommand = $pythonCmd.Source
            $PythonVersionText = $versionText
        }
    }
    catch {
        $PythonCommand = $null
        $PythonVersionText = $null
    }
}

# Try Python Launcher if python.exe is unavailable
# or its version is not supported.

if (-not $PythonCommand) {
    $pyCmd = Get-Command py.exe -ErrorAction SilentlyContinue

    if ($pyCmd) {
        foreach ($candidate in @('3.12', '3.11')) {
            try {
                $versionOutput = & $pyCmd.Source "-$candidate" --version 2>&1
                $versionExitCode = $LASTEXITCODE
                $versionText = ($versionOutput | Out-String).Trim()

                if (
                    $versionExitCode -eq 0 -and
                    $versionText -match '^Python\s+3\.(11|12)\.'
                ) {
                    $PythonCommand = $pyCmd.Source
                    $PythonPrefix = @("-$candidate")
                    $PythonVersionText = $versionText
                    break
                }
            }
            catch {
                continue
            }
        }
    }
}

if (-not $PythonCommand) {
    throw "Python 3.11 or Python 3.12 was not found. Please install one of these versions."
}

Write-Host "Python detected: $PythonVersionText" -ForegroundColor Green
Write-Host "Python executable: $PythonCommand" -ForegroundColor DarkGray

# ------------------------------------------------------------
# 2. Check required project files
# ------------------------------------------------------------

$RequiredFiles = @(
    'main.py',
    'permission_extension.py',
    'store_inventory\__init__.py',
    'store_inventory\permissions\__init__.py',
    'store_inventory\permissions\composition.py',
    'store_inventory\permissions\defaults.py',
    'store_inventory\permissions\domain.py',
    'store_inventory\permissions\http.py',
    'store_inventory\permissions\legacy_policy.py',
    'store_inventory\permissions\ports.py',
    'store_inventory\permissions\registry.py',
    'store_inventory\permissions\service.py',
    'store_inventory\permissions\sqlite_groups.py',
    'static\permission_client.js',
    'requirements.txt',
    'static\index.html',
    'static\index_V2_5_5.html',
    'scripts\verify_project.py',
    'scripts\inspect_database.py',
    'scripts\prepare_release.py',
    'scripts\generate_lan_certificate.py',
    'tests\test_upgrade_integration.py',
    'tests\test_frontend_static.py',
    'tests\test_https_certificate.py',
    'tests\test_release_prepare.py',
    'tests\test_release_database_migration.py',
    'tests\test_v150_mobile_service.py',
    'tests\test_v150_migration_backup.py',
    'tests\test_v150_round4_backup_identity_scanner.py',
    'install_autostart.ps1',
    'uninstall_autostart.ps1',
    'start_server.bat',
    'stop_server.bat',
    'check_server.ps1'
)

foreach ($file in $RequiredFiles) {
    $fullPath = Join-Path $ProjectDir $file

    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        throw "Missing required file: $file"
    }
}

# Validate source and any existing runtime databases without bundling them.
Invoke-Python -Arguments @((Join-Path $ProjectDir 'scripts\verify_project.py')) `
    -FailureMessage "Project preflight verification failed."

$ReleaseDatabase = Join-Path $ProjectDir 'release\data\inventory.db'
if (Test-Path -LiteralPath $ReleaseDatabase -PathType Leaf) {
    Invoke-Python `
        -Arguments @(
            (Join-Path $ProjectDir 'scripts\verify_project.py'),
            '--database',
            $ReleaseDatabase
        ) `
        -FailureMessage "Release database preflight verification failed."
}

# ------------------------------------------------------------
# 3. Install build dependencies
# ------------------------------------------------------------

Write-Host ""
Write-Host "Installing build dependencies..." -ForegroundColor Cyan

Invoke-Python `
    -Arguments @(
        '-m',
        'pip',
        'install',
        '--upgrade',
        'pip'
    ) `
    -FailureMessage "pip upgrade failed."

Invoke-Python `
    -Arguments @(
        '-m',
        'pip',
        'install',
        '-r',
        (Join-Path $ProjectDir 'requirements.txt')
    ) `
    -FailureMessage "Installing requirements.txt failed."

# Explicitly install the required packaging dependencies.

Invoke-Python `
    -Arguments @(
        '-m',
        'pip',
        'install',
        'python-multipart>=0.0.9',
        'pyinstaller>=6.0,<7'
    ) `
    -FailureMessage "Installing python-multipart or PyInstaller failed."

# ------------------------------------------------------------
# 4. Verify critical Python modules
# ------------------------------------------------------------

Write-Host ""
Write-Host "Checking critical Python modules..." -ForegroundColor Cyan

# IMPORTANT:
# Do not use print("...") in this -c argument.
# Windows PowerShell 5.1 may strip embedded quotes when
# passing native command arguments to Python.
#
# This import-only statement contains no inner quotation marks.

$verifyCode = 'import fastapi; import uvicorn; import openpyxl; import multipart; import pydantic; import PyInstaller; import cryptography'

Invoke-Python `
    -Arguments @(
        '-c',
        $verifyCode
    ) `
    -FailureMessage "Critical dependency verification failed."

Write-Host "Critical modules imported successfully." -ForegroundColor Green
Write-Host "Dependency verification passed." -ForegroundColor Green

# ------------------------------------------------------------
# 5. Clean temporary build folders
# ------------------------------------------------------------

Write-Host ""
Write-Host "Cleaning previous build files..." -ForegroundColor Cyan

foreach ($folder in @('build', 'dist')) {
    $path = Join-Path $ProjectDir $folder

    if (Test-Path -LiteralPath $path) {
        Remove-Item `
            -LiteralPath $path `
            -Recurse `
            -Force
    }
}

# IMPORTANT:
# Do not delete release\data.
# Do not delete inventory.db.
# Do not delete config.json.
# Do not delete existing backups or purchase files.

$releaseDir = Join-Path $ProjectDir 'release'

if (-not (Test-Path -LiteralPath $releaseDir)) {
    New-Item `
        -ItemType Directory `
        -Path $releaseDir `
        -Force |
        Out-Null
}

$runtimeFolders = @('data', 'logs', 'data\backups', 'data\purchase')
foreach ($folder in $runtimeFolders) {
    $path = Join-Path $releaseDir $folder
    if (-not (Test-Path -LiteralPath $path)) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
}

Invoke-Python `
    -Arguments @(
        (Join-Path $ProjectDir 'scripts\prepare_release.py'),
        '--project-dir',
        $ProjectDir,
        '--no-seed'
    ) `
    -FailureMessage "Preparing release data/config failed."

# ------------------------------------------------------------
# 6. Build one-file Windows EXE
# ------------------------------------------------------------

Write-Host ""
Write-Host "Building Windows EXE..." -ForegroundColor Cyan
Write-Host "Please wait for PyInstaller to finish." -ForegroundColor Yellow

$staticDir = Join-Path $ProjectDir 'static'
$mainFile = Join-Path $ProjectDir 'main.py'

$staticDataArg = "$($staticDir);static"

$pyInstallerArgs = @(
    '-m',
    'PyInstaller',

    '--noconfirm',
    '--clean',
    '--onefile',
    '--windowed',

    '--name',
    'StoreInventoryServer',

    '--distpath',
    (Join-Path $ProjectDir 'dist'),

    '--workpath',
    (Join-Path $ProjectDir 'build'),

    '--specpath',
    $ProjectDir,

    '--add-data',
    $staticDataArg,

    '--collect-all',
    'fastapi',

    '--collect-all',
    'uvicorn',

    '--collect-all',
    'openpyxl',

    '--collect-all',
    'multipart',

    '--collect-all',
    'pydantic',

    '--collect-all',
    'starlette',

    '--hidden-import',
    'multipart',

    '--hidden-import',
    'multipart.multipart',

    '--hidden-import',
    'uvicorn.logging',

    '--hidden-import',
    'uvicorn.lifespan.on',

    '--hidden-import',
    'uvicorn.protocols.http.auto',

    '--hidden-import',
    'uvicorn.protocols.websockets.auto',

    '--hidden-import',
    'openpyxl',

    $mainFile
)

Invoke-Python `
    -Arguments $pyInstallerArgs `
    -FailureMessage "PyInstaller returned a failure code."

# ------------------------------------------------------------
# 7. Verify build output
# ------------------------------------------------------------

$builtExe = Join-Path $ProjectDir 'dist\StoreInventoryServer.exe'

if (-not (Test-Path -LiteralPath $builtExe -PathType Leaf)) {
    throw "Build output was not found: $builtExe"
}

$builtFile = Get-Item -LiteralPath $builtExe

if ($builtFile.Length -lt 1000000) {
    throw "The generated EXE is unexpectedly small. Build may be incomplete."
}

Write-Host "Build output verification passed." -ForegroundColor Green

# ------------------------------------------------------------
# 8. Prepare release folders and copy EXE
# ------------------------------------------------------------

Write-Host ""
Write-Host "Preparing release folder..." -ForegroundColor Cyan

$runtimeFolders = @(
    'data',
    'logs',
    'data\backups',
    'data\purchase'
)

foreach ($folder in $runtimeFolders) {
    $path = Join-Path $releaseDir $folder

    if (-not (Test-Path -LiteralPath $path)) {
        New-Item `
            -ItemType Directory `
            -Path $path `
            -Force |
            Out-Null
    }
}

$targetExe = Join-Path $releaseDir '库存管理服务器.exe'

try {
    Copy-Item `
        -LiteralPath $builtExe `
        -Destination $targetExe `
        -Force
}
catch {
    throw "Could not replace the EXE. Close the running inventory server and try again. Details: $($_.Exception.Message)"
}

# ------------------------------------------------------------
# 9. Final verification
# ------------------------------------------------------------

if (-not (Test-Path -LiteralPath $targetExe -PathType Leaf)) {
    throw "Final EXE was not found: $targetExe"
}

$targetInfo = Get-Item -LiteralPath $targetExe

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host " BUILD COMPLETED" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green

Write-Host ""
Write-Host "EXE:"
Write-Host $targetExe -ForegroundColor Green

Write-Host ""
Write-Host "Size:"
Write-Host ("{0:N2} MB" -f ($targetInfo.Length / 1MB))

Write-Host ""
Write-Host "Modified:"
Write-Host $targetInfo.LastWriteTime

Write-Host ""
Write-Host "Existing release\data files were not deleted." -ForegroundColor Green
Write-Host "Existing inventory.db and config.json were not overwritten." -ForegroundColor Green
Write-Host "Runtime computer does not need Python installed." -ForegroundColor Green

Write-Host ""
Write-Host "Build process finished." -ForegroundColor Green