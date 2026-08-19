# One-time: register a daily Task Scheduler job (Windows crontab).
# Your PC clock is UTC+8; Korea session is 09:00 KST = 08:00 here.
# Default start 07:30 local so the process is up before the open.

param(
    [string]$At = "07:30",
    [string]$TaskName = "krx-toss-run"
)

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Starter = Join-Path $Root "scripts\start-krx-toss.ps1"
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
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($daily, $logon) `
    -Settings $settings `
    -Principal $principal `
    -Description "Start krx-toss run daily before KRX open, and at logon if not already running." `
    -Force | Out-Null

Write-Host "Registered task '$TaskName'"
Write-Host "  daily at $At local  (KST is +1 hour on this PC)"
Write-Host "  also at Windows logon"
Write-Host ""
Write-Host "Useful:"
Write-Host "  Get-ScheduledTask -TaskName $TaskName"
Write-Host "  Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host ""
Write-Host "Keep this PC from sleeping overnight or the process dies with Windows."
