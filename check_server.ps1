$ErrorActionPreference = 'Continue'
$taskName = '门店库存管理服务器'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

Write-Host '=== 门店库存服务器状态 ===' -ForegroundColor Cyan
if ($task) {
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    Write-Host "后台任务状态：$($task.State)"
    Write-Host "上次运行时间：$($info.LastRunTime)"
    Write-Host ("上次退出码：0x{0:X8}" -f [uint32]$info.LastTaskResult)
    Write-Host "下次运行时间：$($info.NextRunTime)"
} else {
    Write-Host '后台任务：未安装（可运行 install_autostart.ps1 安装）' -ForegroundColor Yellow
}

$runtimeBase = $PSScriptRoot
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'release\库存管理服务器.exe')) {
    $runtimeBase = Join-Path $PSScriptRoot 'release'
} elseif (Test-Path -LiteralPath (Join-Path $PSScriptRoot '库存管理服务器.exe')) {
    $runtimeBase = $PSScriptRoot
}
$configPath = Join-Path $runtimeBase 'data\config.json'
$port = 8000
if (Test-Path -LiteralPath $configPath) {
    try {
        $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($config.port) { $port = [int]$config.port }
    } catch {
        Write-Host "无法读取配置文件：$configPath" -ForegroundColor Yellow
    }
}

$scheme = 'http'
if ($config -and $config.https) { $scheme = 'https' }
Write-Host "访问协议：$scheme（证书必须由手机设备信任）"
Write-Host "检查端口：$port"
try {
    $connection = Test-NetConnection -ComputerName '127.0.0.1' -Port $port -WarningAction SilentlyContinue
    if ($connection.TcpTestSucceeded) {
        Write-Host "服务端口：正在监听（TCP $port）" -ForegroundColor Green
    } else {
        Write-Host "服务端口：未检测到监听（TCP $port）" -ForegroundColor Red
    }
} catch {
    Write-Host "无法检查端口：$($_.Exception.Message)" -ForegroundColor Yellow
}

# TCP listening alone cannot prove that the correct application is healthy.
# For this localhost diagnostic only, allow the local self-signed certificate
# without changing Windows global certificate trust or browser policy.
try {
    $healthUrl = "${scheme}://127.0.0.1:${port}/api/health"
    $healthRequest = [System.Net.HttpWebRequest][System.Net.WebRequest]::Create($healthUrl)
    $healthRequest.Timeout = 4500
    if ($scheme -eq 'https') {
        $healthRequest.ServerCertificateValidationCallback = { $true }
    }
    $healthResponse = $healthRequest.GetResponse()
    try {
        $reader = New-Object IO.StreamReader($healthResponse.GetResponseStream())
        $health = $reader.ReadToEnd()
        $reader.Dispose()
        if ([int]$healthResponse.StatusCode -eq 200 -and $health -match '"ok"\s*:\s*true') {
            Write-Host "实际健康检查：PASS（$healthUrl）" -ForegroundColor Green
        } else {
            Write-Host "实际健康检查：异常（HTTP $([int]$healthResponse.StatusCode)）" -ForegroundColor Red
        }
    } finally { $healthResponse.Dispose() }
} catch {
    Write-Host "实际健康检查：FAIL（$($_.Exception.Message)）" -ForegroundColor Red
}

$caFile = Join-Path $runtimeBase 'data\certs\ca.crt'
if ($scheme -eq 'https' -and (Test-Path -LiteralPath $caFile)) {
    Write-Host "手机信任证书路径：$caFile（只能分发 ca.crt，严禁分发 .key 文件）" -ForegroundColor Yellow
}

$logCandidates = @(
    (Join-Path $runtimeBase 'logs\server.log')
)
foreach ($log in $logCandidates) {
    if (Test-Path -LiteralPath $log) {
        Write-Host "`n=== 最近服务器日志（$log） ===" -ForegroundColor Cyan
        Get-Content -LiteralPath $log -Tail 30
        break
    }
}
