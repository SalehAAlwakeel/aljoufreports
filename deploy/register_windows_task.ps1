<#
.SYNOPSIS
    Registers the weekly pipeline as a Windows Scheduled Task.

.DESCRIPTION
    Creates a task that runs `python -m aljouf_report run` on a schedule, using
    the project's virtual environment. Run this script from an elevated
    PowerShell prompt (scheduled tasks need administrator rights to register).

.PARAMETER DayOfWeek
    Day to run on. Defaults to Sunday, the start of the Saudi work week.

.PARAMETER Time
    Local 24-hour start time. Defaults to 09:00 to match the delivery window
    in the project brief.

.PARAMETER Semimonthly
    Register for the 1st and 15th of each month instead of weekly, matching the
    "كل 1 و15 من كل شهر الساعة 9 صباحاً" cadence in the source document.

.EXAMPLE
    .\deploy\register_windows_task.ps1
    .\deploy\register_windows_task.ps1 -Time 07:30 -DayOfWeek Monday
    .\deploy\register_windows_task.ps1 -Semimonthly
#>

[CmdletBinding()]
param(
    [ValidateSet('Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday')]
    [string]$DayOfWeek = 'Sunday',

    [string]$Time = '09:00',

    [switch]$Semimonthly,

    [string]$TaskName = 'JSDO Weekly Social Report'
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython  = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    Write-Warning "Virtual environment not found at $venvPython"
    Write-Warning "Create it first:  python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt"
    $venvPython = (Get-Command python).Source
    Write-Warning "Falling back to the system interpreter: $venvPython"
}

# -NoProfile keeps startup fast and avoids profile scripts interfering.
$action = New-ScheduledTaskAction `
    -Execute $venvPython `
    -Argument '-m aljouf_report run' `
    -WorkingDirectory $projectRoot

if ($Semimonthly) {
    # Scheduled Tasks has no native "1st and 15th" trigger via this cmdlet, so
    # register a daily trigger and let the task guard the day of month.
    $action = New-ScheduledTaskAction `
        -Execute 'powershell.exe' `
        -Argument "-NoProfile -Command `"if ((Get-Date).Day -in 1,15) { & '$venvPython' -m aljouf_report run }`"" `
        -WorkingDirectory $projectRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At $Time
    $scheduleText = "daily at $Time, running only on the 1st and 15th"
}
else {
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DayOfWeek -At $Time
    $scheduleText = "$DayOfWeek at $Time"
}

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 15) `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Removing the existing task '$TaskName'..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName   $TaskName `
    -Action     $action `
    -Trigger    $trigger `
    -Settings   $settings `
    -Description 'Collects followers, news and comment sentiment, then emails the weekly summary.' `
    -RunLevel   Limited | Out-Null

Write-Host ""
Write-Host "Registered '$TaskName'" -ForegroundColor Green
Write-Host "  Schedule   : $scheduleText"
Write-Host "  Interpreter: $venvPython"
Write-Host "  Directory  : $projectRoot"
Write-Host ""
Write-Host "Run it now to verify:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Check the result:" -ForegroundColor Cyan
Write-Host "  Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "Logs: $projectRoot\logs\pipeline.log"
