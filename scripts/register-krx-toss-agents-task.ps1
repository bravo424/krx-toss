# One-time: register a daily Task Scheduler job for post-close agent runs.
# Your PC clock is UTC+8; 15:30 KST close ≈ 14:30 local — default 14:35 local.

param(
    [string]$At = "14:35",
    [string]$TaskName = "krx-toss-agents"
)

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Starter = Join-Path $Root "scripts\start-krx-toss-agents.ps1"
if (-not (Test-Path $Starter)) {
    throw "Missing $Starter"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Starter`"" `
    -WorkingDirectory $Root

$daily = New-ScheduledTaskTrigger -Daily -At $At
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($daily, $logon) `
    -Settings $settings `
    -Principal $principal `
    -Description "Start krx-toss agents supervisor (PNL/research/QA pipeline)." `
    -Force | Out-Null

Write-Host "Registered task '$TaskName'"
Write-Host "  daily at $At local  (KST is +1 hour on this PC)"
Write-Host "  also at Windows logon"
Write-Host ""
Write-Host "Continuous supervisor (overnight research):"
Write-Host "  krx-toss agents"
Write-Host ""
Write-Host "Useful:"
Write-Host "  Get-ScheduledTask -TaskName $TaskName"
Write-Host "  Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
