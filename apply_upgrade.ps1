param(
    [Parameter(Mandatory=$true)]
    [string]$TargetPath,
    [switch]$DevelopmentTestOnly
)
$ErrorActionPreference = 'Stop'
if (!$DevelopmentTestOnly) {
    throw '这是 V1.5.0 开发检查点，禁止直接覆盖正式系统。仅在独立测试目录使用 -DevelopmentTestOnly 参数试运行。'
}
$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$payloadRoot = Join-Path $sourceRoot 'upgrade_payload'
$target = (Resolve-Path -LiteralPath $TargetPath).Path
if (!(Test-Path (Join-Path $target 'main.py')) -or !(Test-Path (Join-Path $target 'static'))) {
    throw "目标目录不是有效的 V1.4.0 项目目录：$target"
}
if (!(Test-Path $payloadRoot)) { throw "升级源码目录缺失：$payloadRoot" }

$targetMain = Get-Content -LiteralPath (Join-Path $target 'main.py') -Raw -Encoding UTF8
if ($targetMain -notmatch 'APP_VERSION\s*=\s*["'']1\.4\.0["'']') {
    throw '目标源码版本不是 1.4.0。为防止覆盖错误版本，本升级脚本只接受 V1.4.0 正式基线。'
}
$running = Get-Process -Name 'StoreInventoryServer','库存管理服务器' -ErrorAction SilentlyContinue
if ($running) { throw '检测到服务端进程仍在运行。请先关闭旧版服务端，再重新执行升级。' }
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backup = Join-Path $target "_pre_upgrade_backup_$stamp"
New-Item -ItemType Directory -Path $backup -Force | Out-Null
foreach ($relative in @('data','release\data')) {
    $from = Join-Path $target $relative
    if (Test-Path $from) {
        $to = Join-Path $backup $relative
        New-Item -ItemType Directory -Path (Split-Path $to -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $from -Destination $to -Recurse -Force
    }
}
Get-ChildItem -LiteralPath $payloadRoot -Force | Where-Object { $_.Name -ne 'tests_update.zip' } | ForEach-Object {
    $destination = Join-Path $target $_.Name
    if ($_.PSIsContainer) {
        if (!(Test-Path $destination)) { New-Item -ItemType Directory -Path $destination -Force | Out-Null }
        Get-ChildItem -LiteralPath $_.FullName -Force | Copy-Item -Destination $destination -Recurse -Force
    } else {
        Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
    }
}
$testsArchive = Join-Path $payloadRoot 'tests_update.zip'
if (Test-Path $testsArchive) {
    $testStage = Join-Path $env:TEMP "StoreInventory_V1_4_0_tests_$stamp"
    Expand-Archive -LiteralPath $testsArchive -DestinationPath $testStage -Force
    if (Test-Path (Join-Path $testStage 'tests')) {
        $targetTests = Join-Path $target 'tests'
        if (!(Test-Path -LiteralPath $targetTests)) {
            New-Item -ItemType Directory -Path $targetTests -Force | Out-Null
        }
        Get-ChildItem -LiteralPath (Join-Path $testStage 'tests') -Force | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $targetTests -Recurse -Force
        }
    }
    if (Test-Path (Join-Path $testStage 'test_smoke.py')) {
        Copy-Item -LiteralPath (Join-Path $testStage 'test_smoke.py') -Destination (Join-Path $target 'test_smoke.py') -Force
    }
    Remove-Item -LiteralPath $testStage -Recurse -Force
}
Write-Host "V1.5.0 源码已覆盖。数据备份位置：$backup" -ForegroundColor Green
Write-Host '数据库、运行配置、备份、日志和采购文件已保留。请在目标目录运行 build_windows.bat。' -ForegroundColor Cyan
