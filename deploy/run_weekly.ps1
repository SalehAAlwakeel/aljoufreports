<#
.SYNOPSIS
    Wrapper the Scheduled Task (or a human) can call to run one weekly cycle.
.DESCRIPTION
    Activates the project virtual environment, runs the pipeline, and writes a
    timestamped transcript next to the application log.
#>

[CmdletBinding()]
param(
    [switch]$SkipEmail
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$logDir = Join-Path $projectRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$transcript = Join-Path $logDir ("run-{0}.log" -f (Get-Date -Format 'yyyy-MM-dd-HHmm'))

$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

# Force UTF-8 so Arabic output is not mangled by the console code page.
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

$arguments = @('-m', 'aljouf_report', 'run')
if ($SkipEmail) { $arguments += '--skip-email' }

Write-Host "Running: $python $($arguments -join ' ')"
& $python @arguments *>&1 | Tee-Object -FilePath $transcript
$exitCode = $LASTEXITCODE

Write-Host "Finished with exit code $exitCode. Transcript: $transcript"
exit $exitCode
