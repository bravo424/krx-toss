# Launch `krx-toss agents` (continuous supervisor) if not already running.
# Register once (daily 14:35 local = 15:35 KST, after regular close):
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\nasan\krx-toss-trading\scripts\register-krx-toss-agents-task.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$already = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'krx-toss.exe'" |
    Where-Object { $_.CommandLine -match 'krx-toss(\.exe)?(\s+agents)?|"krx-toss"|krx_toss.*agents' }
if ($already) {
    Write-Host "krx-toss agents already running (pid $($already[0].ProcessId)); skip"
    exit 0
}

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
$logDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd"
$consoleLog = Join-Path $logDir "agents-$stamp.log"

if (Test-Path $venvPython) {
    $exe = $venvPython
    $argList = @("-m", "krx_toss", "agents")
} elseif (Get-Command krx-toss -ErrorAction SilentlyContinue) {
    $exe = (Get-Command krx-toss).Source
    $argList = @("agents")
} else {
    Write-Error "No .venv python and no krx-toss on PATH. pip install -e . first."
}

Write-Host "starting $exe $($argList -join ' ') in $Root"
& $exe @argList *>> $consoleLog
exit $LASTEXITCODE
