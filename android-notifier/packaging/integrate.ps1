param([Parameter(Mandatory=$true)][string]$Root, [switch]$Preflight)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
$appRoot=[IO.Path]::GetFullPath((Join-Path $Root 'app'))
$scriptRoot=Join-Path $appRoot 'android-notifier'
$owner='Chaoxing Android local course schedule: ' + $scriptRoot
$task=Get-ScheduledTask -TaskName 'ChaoxingAndroid-Weekly' -TaskPath '\' -ErrorAction SilentlyContinue
if ($task -and $task.Description -ne $owner) { throw '已有另一目录的学习通计划任务。请继续使用原程序；若要迁移，先在原控制台取消全部时段，再重新安装。此安装未修改旧任务。' }
$desktop=[Environment]::GetFolderPath('DesktopDirectory')
$link=Join-Path $desktop '学习通通知控制台.lnk'
$shell=New-Object -ComObject WScript.Shell
$shortcut=$shell.CreateShortcut($link)
$launcher=Join-Path $scriptRoot 'launcher.py'
if ((Test-Path -LiteralPath $link) -and $shortcut.Arguments -ne ('"'+$launcher+'"')) { throw '桌面已有另一程序的同名入口，请先将旧快捷方式改名后重试。' }
$live=Join-Path $appRoot 'runtime\status.json'
if (Test-Path -LiteralPath $live) {
    $heartbeat=Get-Content -LiteralPath $live -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($heartbeat.state -eq 'watching' -and (Get-Process -Id $heartbeat.pid -ErrorAction SilentlyContinue)) { throw '此安装目录的接收端正在运行，请先停止接收。' }
}
if ((Get-TimeZone).Id -ne 'China Standard Time') { throw '此版本每周计划要求 Windows 时区为北京时间（China Standard Time），请先在系统设置中调整。' }
if ($Preflight) { Write-Output 'PREFLIGHT OK';return }
$python=Join-Path $Root 'python\pythonw.exe'
$request=Join-Path $appRoot 'runtime\install-schedule-request.json'
$plan=Get-Content -LiteralPath (Join-Path $appRoot 'runtime\android\weekly-plan.json') -Raw -Encoding UTF8 | ConvertFrom-Json
@{weekly=$plan.weekly;python=$python} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $request -Encoding UTF8
$oldXml=if($task){Export-ScheduledTask -TaskName 'ChaoxingAndroid-Weekly' -TaskPath '\'}else{$null}
$oldLink=if(Test-Path -LiteralPath $link){[IO.File]::ReadAllBytes($link)}else{$null}
try {
    & (Join-Path $scriptRoot 'register_weekly.ps1') -Request $request
    $shortcut.TargetPath=$python;$shortcut.Arguments='"'+$launcher+'"';$shortcut.WorkingDirectory=$appRoot
    $shortcut.Description='学习通本地模型与知识库，群内监看，一键启停及每周时段'
    $shortcut.Save()
} catch {
    if ($oldXml) { Register-ScheduledTask -TaskName 'ChaoxingAndroid-Weekly' -TaskPath '\' -Xml $oldXml -Force | Out-Null }
    else { $ours=Get-ScheduledTask -TaskName 'ChaoxingAndroid-Weekly' -TaskPath '\' -ErrorAction SilentlyContinue; if ($ours -and $ours.Description -eq $owner) { Unregister-ScheduledTask -TaskName 'ChaoxingAndroid-Weekly' -TaskPath '\' -Confirm:$false } }
    if ($oldLink) { [IO.File]::WriteAllBytes($link,$oldLink) }
    elseif (Test-Path -LiteralPath $link) { Remove-Item -LiteralPath $link }
    throw
} finally { if(Test-Path -LiteralPath $request){Remove-Item -LiteralPath $request} }
Write-Output 'INTEGRATION OK'
