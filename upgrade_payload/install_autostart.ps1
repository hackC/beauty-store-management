$ErrorActionPreference = 'Stop'

$taskName = '门店库存管理服务器'
$exe = Join-Path $PSScriptRoot 'release\库存管理服务器.exe'
if (!(Test-Path -LiteralPath $exe)) {
    $exe = Join-Path $PSScriptRoot '库存管理服务器.exe'
}
if (!(Test-Path -LiteralPath $exe)) {
    throw '未找到库存管理服务器.exe，请先运行 build_windows.bat 构建。'
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principalCheck = New-Object Security.Principal.WindowsPrincipal($identity)
if (!$principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '安装开机后台任务需要管理员权限。请右键 PowerShell，选择“以管理员身份运行”。'
}

$workingDirectory = Split-Path -Parent $exe
$action = New-ScheduledTaskAction `
    -Execute $exe `
    -Argument '--headless' `
    -WorkingDirectory $workingDirectory

# AtStartup + SYSTEM means the server starts before an employee logs in.
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal `
    -UserId 'SYSTEM' `
    -LogonType ServiceAccount `
    -RunLevel Highest

# Restart a failed server process; do not impose a run-time limit.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description '门店库存与每日排班服务器：开机后台启动，异常退出后自动重启。' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $taskName
Write-Host '已安装开机后台运行任务。' -ForegroundColor Green
Write-Host '运行账户：SYSTEM；触发条件：Windows 开机；异常恢复：每 1 分钟重试，最多 999 次。'
Write-Host "程序路径：$exe"
Write-Host '注意：这是 Windows 任务计划程序后台任务，不是注册到服务控制管理器（services.msc）的原生 Windows Service。'
