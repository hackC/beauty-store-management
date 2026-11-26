$ErrorActionPreference = 'Stop'
$taskName = '门店库存管理服务器'

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    try { Disable-ScheduledTask -TaskName $taskName | Out-Null } catch {}
    try { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue } catch {}
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host '已停止并卸载门店库存后台任务。' -ForegroundColor Green
} else {
    Write-Host '未发现已安装的门店库存后台任务。' -ForegroundColor Yellow
}
