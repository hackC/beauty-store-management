$port = 8000
$rule = Get-NetFirewallRule -DisplayName '门店库存管理系统局域网访问' -ErrorAction SilentlyContinue
if ($rule) { Remove-NetFirewallRule -DisplayName '门店库存管理系统局域网访问' }
New-NetFirewallRule -DisplayName '门店库存管理系统局域网访问' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -RemoteAddress LocalSubnet -Profile Private | Out-Null
Write-Host "已允许私人网络中的设备访问 TCP $port。" -ForegroundColor Green
Write-Host '如果你在设置中修改了端口，需要同步修改防火墙规则。'
