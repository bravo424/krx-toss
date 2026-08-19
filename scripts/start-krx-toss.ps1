# Launch `krx-toss run` if it is not already running.
# Register once (daily 07:30 local = 08:30 KST on this UTC+8 PC):
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\nasan\krx-toss-trading\scripts\register-krx-toss-task.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$already = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'krx-toss.exe'" |
    Where-Object { $_.CommandLine -match 'krx-toss(\.exe)?(\s+run)?|"krx-toss"|krx_toss' }
if ($already) {
    Write-Host "krx-toss already running (pid $($already[0].ProcessId)); skip"
    exit 0
}

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
$logDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd"
$consoleLog = Join-Path $logDir "scheduler-$stamp.log"

if (Test-Path $venvPython) {
    $exe = $venvPython
    $argList = @("-m", "krx_toss", "run")
} elseif (Get-Command krx-toss -ErrorAction SilentlyContinue) {
    $exe = (Get-Command krx-toss).Source
    $argList = @("run")
} else {
    Write-Error "No .venv python and no krx-toss on PATH. pip install -e . first."
}

Write-Host "starting $exe $($argList -join ' ') in $Root"
& $exe @argList *>> $consoleLog
exit $LASTEXITCODE
