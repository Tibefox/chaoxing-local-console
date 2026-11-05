$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
@(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^(pythonw?|llama-server|adb|powershell|pwsh|cmd|conhost|node|MuMuNxDevice|MuMuNxMain|MuMuNxService)\.exe$'
} | ForEach-Object {
    [pscustomobject]@{
        pid=[int]$_.ProcessId;parent=[int]$_.ParentProcessId;name=$_.Name
        executable=$_.ExecutablePath;command=$_.CommandLine
        created=$_.CreationDate.ToUniversalTime().ToFileTimeUtc()
    }
}) | ConvertTo-Json -Compress
