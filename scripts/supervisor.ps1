<#
.SYNOPSIS
Keeps the collectors running across sessions, reboots and crashes.

.DESCRIPTION
The snapshotter and emit_baselines are foreground jobs. When the shell that
started them goes away, ingestion stops silently and the G0 clock resets --
that is what produced the 28-hour gap on 2026-09-02. Windows Task Scheduler
already solves this, so this script only registers two tasks and gets out of
the way; there is no daemon here to babysit.

The restart mechanism is the repeating trigger, not failure detection. Each
task re-fires on an interval with MultipleInstances=IgnoreNew: if the job is
alive the new instance is discarded, if it died the tick restarts it. That
covers a crash, a clean exit and a missed wake-up with one mechanism.

Tasks run as the current user at logon. Running them with no user logged on
needs stored credentials and an elevated install, which is not worth it for a
research pipeline on a desktop.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts\supervisor.ps1 -Install
powershell -ExecutionPolicy Bypass -File scripts\supervisor.ps1 -Status
powershell -ExecutionPolicy Bypass -File scripts\supervisor.ps1 -Uninstall
#>
[CmdletBinding(DefaultParameterSetName = 'Status')]
param(
    [Parameter(ParameterSetName = 'Install')][switch]$Install,
    [Parameter(ParameterSetName = 'Uninstall')][switch]$Uninstall,
    [Parameter(ParameterSetName = 'Status')][switch]$Status,
    # Deliberately not a ValidateSet. One was here listing 'snapshot','emit', and adding
    # the gefs task to $Tasks without updating it made every pmv-gefs run fail parameter
    # binding -- exit 1, before any log existed, so the only symptom was a task result of
    # 1 and an empty logs directory. The job list has one home now: $Tasks.
    [Parameter(ParameterSetName = 'Run', Mandatory = $true)]
    [string]$Run
)

$ErrorActionPreference = 'Stop'

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$PythonExe  = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$LogDir     = Join-Path $RepoRoot 'logs'
$ThisScript = $PSCommandPath

# The snapshotter holds one pooled connection and sweeps continuously, so a tick
# only ever needs to notice that it died. The emitter is single-pass and must run
# often enough to catch a horizon inside its tolerance window.
$SnapshotIntervalMin = 15
$EmitIntervalMin     = 4
# GEFS publishes four runs a day, so hourly is already far finer than the data
# changes. The reason to fetch this often is point-in-time granularity: a prediction
# uses the newest forecast that existed before it, and hourly bounds how stale that
# can be.
$GefsIntervalMin     = 60

$Tasks = @{
    snapshot = @{
        Name        = 'pmv-snapshot'
        Description = 'Prediction market validation: order book snapshotter (restarts if not running)'
        Arguments   = '-m scripts.snapshot --minutes 0'
        IntervalMin = $SnapshotIntervalMin
        TimeLimit   = [TimeSpan]::Zero          # runs forever by design
    }
    emit = @{
        Name        = 'pmv-emit'
        Description = 'Prediction market validation: baseline emitter (cannot be backfilled)'
        Arguments   = '-m scripts.emit_baselines'
        IntervalMin = $EmitIntervalMin
        TimeLimit   = (New-TimeSpan -Hours 1)
    }
    gefs = @{
        Name        = 'pmv-gefs'
        Description = 'Prediction market validation: GEFS ensemble feed (cannot be backfilled)'
        Arguments   = '-m scripts.fetch_gefs'
        IntervalMin = $GefsIntervalMin
        TimeLimit   = (New-TimeSpan -Minutes 30)
    }
}

function Invoke-Job {
    param([string]$JobName)

    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
    $log = Join-Path $LogDir ("{0}-{1:yyyyMMdd}.log" -f $JobName, (Get-Date))

    # The log is opened before anything can fail. A job that dies during setup used to
    # leave no file at all, so a broken collector and a collector that had simply never
    # been due looked identical from the logs directory.
    "=== $JobName started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" |
        Out-File $log -Append -Encoding utf8

    if (-not $Tasks.ContainsKey($JobName)) {
        "FATAL: unknown job '$JobName'. Known: $($Tasks.Keys -join ', ')" |
            Out-File $log -Append -Encoding utf8
        exit 2
    }
    $spec = $Tasks[$JobName]

    try {
        Set-Location $RepoRoot
        # Redirection is done by cmd rather than PowerShell: 5.1 wraps a native
        # command's stderr in ErrorRecords when redirected, which corrupts the log.
        & cmd.exe /c "`"$PythonExe`" $($spec.Arguments) >> `"$log`" 2>&1"
        exit $LASTEXITCODE
    }
    catch {
        "FATAL: $($_.Exception.Message)" | Out-File $log -Append -Encoding utf8
        exit 3
    }
}

function Install-Tasks {
    if (-not (Test-Path $PythonExe)) { throw "venv python not found at $PythonExe -- run uv sync first" }

    foreach ($jobName in $Tasks.Keys) {
        $spec = $Tasks[$jobName]

        # -WindowStyle Hidden matters beyond cosmetics: without it the job attaches to
        # whatever console started it and dies with STATUS_CONTROL_C_EXIT when that
        # shell exits. Observed 2026-09-03 -- the snapshotter was killed seconds after
        # a manual Start-ScheduledTask from a short-lived shell, logging nothing.
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
            -Argument ("-NoProfile -NonInteractive -WindowStyle Hidden " +
                       "-ExecutionPolicy Bypass -File `"$ThisScript`" -Run $jobName") `
            -WorkingDirectory $RepoRoot

        # A repeating trigger from a start time in the past fires immediately and
        # then every interval, which is both the starter and the restarter.
        $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(-1) `
            -RepetitionInterval (New-TimeSpan -Minutes $spec.IntervalMin)
        $atLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

        $settings = New-ScheduledTaskSettingsSet `
            -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit $spec.TimeLimit `
            -StartWhenAvailable `
            -DontStopOnIdleEnd `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)

        Register-ScheduledTask -TaskName $spec.Name -Action $action `
            -Trigger @($trigger, $atLogon) -Settings $settings `
            -Description $spec.Description -Force | Out-Null

        Write-Host "registered $($spec.Name)  (every $($spec.IntervalMin) min, restarts if not running)"
    }

    Write-Host ""
    Write-Host "Logs: $LogDir\<job>-<date>.log"
    Write-Host "Collection stops if this machine sleeps or the user logs out."
    Write-Host "NOTE: -Force re-registration stops a running job. Each task's next tick"
    Write-Host "      restarts it, so re-installing mid-window costs up to one interval."
}

function Uninstall-Tasks {
    foreach ($jobName in $Tasks.Keys) {
        $name = $Tasks[$jobName].Name
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask  -TaskName $name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "removed $name"
        }
        else { Write-Host "$name not registered" }
    }
}

function Show-Status {
    foreach ($jobName in $Tasks.Keys) {
        $name = $Tasks[$jobName].Name
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($null -eq $task) { Write-Host "$name : NOT REGISTERED"; continue }

        $info = Get-ScheduledTaskInfo -TaskName $name

        # 0x41301 "currently running" and 0x41303 "has not yet run" are states, not
        # faults. Anything else is a job that ran and failed, which is worth naming
        # loudly: pmv-gefs sat at result=1 for hours and the only other symptom was a
        # log file that did not exist.
        $rc = $info.LastTaskResult
        $verdict = if ($rc -eq 0 -or $rc -eq 267009 -or $rc -eq 267011) { "" } else { "   <-- FAILING" }
        Write-Host "$name : $($task.State)  last=$($info.LastRunTime)  result=$rc  next=$($info.NextRunTime)$verdict"

        $log = Join-Path $LogDir ("{0}-{1:yyyyMMdd}.log" -f $jobName, (Get-Date))
        if (Test-Path $log) {
            Get-Content $log -Tail 3 | ForEach-Object { Write-Host "    $_" }
        }
        else {
            Write-Host "    (no log today at $log)"
        }
    }
}

switch ($PSCmdlet.ParameterSetName) {
    'Run'       { Invoke-Job -JobName $Run }
    'Install'   { Install-Tasks }
    'Uninstall' { Uninstall-Tasks }
    default     { Show-Status }
}
