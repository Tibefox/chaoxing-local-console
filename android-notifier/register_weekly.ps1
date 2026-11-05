param([Parameter(Mandatory=$true)][string]$Request)
$ErrorActionPreference = 'Stop'
$plan = Get-Content -LiteralPath $Request -Raw -Encoding UTF8 | ConvertFrom-Json
$name = 'ChaoxingAndroid-Weekly'
$owner = 'Chaoxing Android local course schedule: ' + $PSScriptRoot
$old = Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue
if ($old -and $old.Description -ne $owner) { throw 'Task name already in use' }
if (-not $plan.weekly) {
    if ($old) { Unregister-ScheduledTask -TaskName $name -TaskPath '\' -Confirm:$false }
    return
}
if ((Get-TimeZone).Id -ne 'China Standard Time') { throw 'Windows time zone must be China Standard Time' }
$account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$dayNames = 'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'
$triggers = @(
    foreach ($rule in $plan.weekly) {
        $days = @($rule.days | ForEach-Object { $dayNames[[int]$_] })
        New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $days -At ([DateTime]::Today.Add([TimeSpan]::Parse($rule.start)))
    }
    New-ScheduledTaskTrigger -AtLogOn -User $account
)
$script = Join-Path $PSScriptRoot 'weekly_control.py'
$action = New-ScheduledTaskAction -Execute $plan.python -Argument ('"' + $script + '" --trigger') -WorkingDirectory $PSScriptRoot
$user = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$options = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -TaskPath '\' -Action $action -Trigger $triggers -Principal $user -Settings $options -Description $owner -Force | Out-Null
