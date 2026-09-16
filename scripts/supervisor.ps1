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
    [string]$Run,
    # Put the machine back to sleep after the band, but never while it is in use.
    [Parameter(ParameterSetName = 'SleepIfIdle')][switch]$SleepIfIdle,
    [Parameter(ParameterSetName = 'SleepIfIdle')][int]$IdleMinutes = 10
)

$ErrorActionPreference = 'Stop'

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$PythonExe  = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$LogDir     = Join-Path $RepoRoot 'logs'
$ThisScript = $PSCommandPath

# The snapshotter holds one pooled connection and sweeps continuously, so a tick
# only ever needs to notice that it died. The emitter is single-pass and must run
# often enough to catch a horizon inside its tolerance window.
# The daily collection band, in local time. Every Kalshi daily temperature market
# closes just after midnight US local, so its T-24h horizon lands at ~01:00 US local --
# 07:00 to 10:00 in Spain across the four US timezones. The band brackets all of them
# with margin (the dense window opens 15 min before each horizon) and covers 188 of 189
# observed weather horizons. The last horizon plus its window closes at 10:15, so the
# 11:30 end is slack, not coverage.
#
# Shrinking it does not thin the sample evenly, it deletes whole timezones: starting at
# 08:00 loses every East Coast city. For a weather study, biasing by climate region is
# close to the worst available bias, so the band is a research parameter, not a
# convenience. It is declared in docs/pre_declaration.md, Amendment 3.
# A whole number of hours, deliberately. Task Scheduler rounds a repetition duration up
# to the hour, so a 5.5h band became a 6h repetition window running to 11:30 while
# everything else assumed 11:00. Matching the two removes the discrepancy rather than
# compensating for it.
$BandStart      = '05:30'
$BandDurationH  = 6
$BandEnd        = ([datetime]$BandStart).AddHours($BandDurationH).ToString('HH:mm')

# Sleep must come after the last repetition, not after the band's nominal end. At 11:05
# with repetitions running to 11:30, pmv-emit was still due at 11:06 -- and every
# collector carries WakeToRun, so the next tick would have woken the machine straight
# back up. Ten minutes past the last tick, plus the snapshotter's own --until stop, so
# nothing is suspended mid-flush.
$SleepTaskName  = 'pmv-sleep'
$SleepAt        = ([datetime]$BandEnd).AddMinutes(10).ToString('HH:mm')

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
        # --until, not --minutes. Task Scheduler rounds a repetition duration up to whole
        # hours, so a restart tick can fire after the band should have closed; a
        # duration-bounded run starting then would collect for another 5.5 hours and hold
        # the machine awake until late afternoon. An absolute stop ends the band on time
        # no matter when the instance started.
        Arguments   = "-m scripts.snapshot --until $BandEnd"
        IntervalMin = $SnapshotIntervalMin
        # -Hours is [int]. `-Hours ($BandDurationH + 0.5)` rounded 6.5 to 6, so the half
        # hour of grace was silently zero and the limit landed on 11:30:02 -- the exact
        # moment the tick budget runs out. Task Scheduler killed the snapshotter mid-exit
        # every day: no final flush(), no stats line in the log, LastTaskResult stuck at
        # 1. That result code is the signal Amendment 10 records as sitting unused during
        # the 09-13 outage, and a permanent 1 cannot flag anything.
        TimeLimit   = (New-TimeSpan -Hours $BandDurationH -Minutes 30)
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
    # Outcomes are the one thing here that *can* be backfilled, so this runs once near
    # the end of the band rather than on a tight interval. Without it the scorer has
    # nothing to join predictions against, which is how outcomes sat empty for a day.
    resolutions = @{
        Name        = 'pmv-resolutions'
        Description = 'Prediction market validation: settle outcomes for catalogued markets'
        Arguments   = '-m scripts.watch_resolutions --catalogue'
        IntervalMin = 240
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

function Get-IdleSeconds {
    # Seconds since the last keyboard or mouse input in this session.
    #
    # GetLastInputInfo is session-scoped, which is exactly what is wanted: the question
    # is whether *this user* is at the machine, not whether the CPU is busy. A collector
    # sweeping hundreds of order books is not "in use" by any definition that should
    # postpone sleep.
    if (-not ('PmvIdle' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PmvIdle {
    [StructLayout(LayoutKind.Sequential)]
    private struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
    [DllImport("user32.dll")] private static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
    public static double Seconds() {
        LASTINPUTINFO lii = new LASTINPUTINFO();
        lii.cbSize = (uint)Marshal.SizeOf(typeof(LASTINPUTINFO));
        if (!GetLastInputInfo(ref lii)) { return -1; }
        return (double)((uint)Environment.TickCount - lii.dwTime) / 1000.0;
    }
}
'@
    }
    return [PmvIdle]::Seconds()
}

function Invoke-SleepIfIdle {
    param([int]$IdleMinutes)

    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
    $log = Join-Path $LogDir ("sleep-{0:yyyyMMdd}.log" -f (Get-Date))
    function Note($text) {
        "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $text" | Out-File $log -Append -Encoding utf8
    }

    # Both readings are taken before either is acted on, so every log line carries the
    # full picture. Checking the collectors first and returning early hid the idle time
    # entirely, which made the in-use guard -- the one thing this job exists for --
    # impossible to observe without waiting for a real 11:05.
    $idle = Get-IdleSeconds
    $running = @(Get-Process python -ErrorAction SilentlyContinue)
    # No :N0. On a Spanish locale that renders 11058 as "11.058", which reads as eleven
    # seconds to anyone scanning the log and made a correct SLEEP decision look like a
    # bug. Plain integers, no thousands separator, in a log meant to be believed.
    $state = "idle=$([int]$idle)s threshold=$($IdleMinutes * 60)s collectors=$($running.Count)"

    # Never suspend on top of a running collector. --until should already have stopped
    # the snapshotter, but a sweep that overran would otherwise be cut off mid-flush,
    # and the lost buffer is not recoverable.
    if ($running.Count -gt 0) {
        Note "SKIP: collectors still running.  $state"
        return 0
    }
    if ($idle -lt 0) {
        Note "SKIP: could not read idle time; refusing to guess.  $state"
        return 0
    }
    if ($idle -lt ($IdleMinutes * 60)) {
        Note "SKIP: machine in use.  $state"
        return 0
    }

    Note "SLEEP: suspending.  $state"
    Add-Type -AssemblyName System.Windows.Forms
    # Suspend, not Hibernate: S3 is what the wake timer resumes from most reliably, and
    # this machine reports hybrid sleep as unavailable so Suspend should not be
    # promoted to S4.
    [System.Windows.Forms.Application]::SetSuspendState(
        [System.Windows.Forms.PowerState]::Suspend, $false, $false) | Out-Null
    return 0
}

function Install-Tasks {
    if (-not (Test-Path $PythonExe)) { throw "venv python not found at $PythonExe -- run uv sync first" }

    # The snapshotter runs the whole band, so its kill timer must outlast the band or
    # Task Scheduler ends the process instead of letting it exit. Asserted rather than
    # trusted: the previous expression truncated to exactly the band length and nothing
    # said so for twelve days.
    if ($Tasks.snapshot.TimeLimit -le (New-TimeSpan -Hours $BandDurationH)) {
        throw ("snapshot TimeLimit {0} does not outlast the {1}h band" -f
               $Tasks.snapshot.TimeLimit, $BandDurationH)
    }

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

        # Daily at the band opening, repeating within it and stopping at its end. The
        # repetition is still what restarts a dead job; it just no longer runs around
        # the clock, because the machine is not on around the clock.
        $trigger = New-ScheduledTaskTrigger -Daily -At $BandStart
        $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $BandStart `
            -RepetitionInterval (New-TimeSpan -Minutes $spec.IntervalMin) `
            -RepetitionDuration (New-TimeSpan -Hours $BandDurationH)).Repetition

        # StartWhenAvailable is deliberately OFF, and this is not a detail.
        #
        # With it on, Task Scheduler treats every band occurrence that the machine slept
        # through as a missed run to be caught up "as soon as possible" -- and WakeToRun
        # then wakes the machine to do it. Observed 2026-09-03: the machine was suspended
        # at 22:55:52 and woke at 22:55:55, three seconds later, because pmv-emit had
        # missed occurrences from a StartBoundary in the past. It then stayed awake all
        # night. powercfg /lastwake named the task outright.
        #
        # A late catch-up run is worthless here anyway: it would collect outside the
        # band, which is exactly what the band exists to prevent. The daily trigger plus
        # WakeToRun is what starts collection; nothing needs to run late.
        $settings = New-ScheduledTaskSettingsSet `
            -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit $spec.TimeLimit `
            -DontStopOnIdleEnd `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
        $settings.StartWhenAvailable = $false

        # Wake the machine for the band. This is the whole point: the horizons are at
        # 07:00-10:00 local and nobody is going to be awake to press a key. It works
        # from sleep only -- a machine shut down needs BIOS wake-on-RTC instead, and
        # nothing here can detect the difference, so -Status is what to check.
        $settings.WakeToRun = $true

        Register-ScheduledTask -TaskName $spec.Name -Action $action `
            -Trigger $trigger -Settings $settings `
            -Description $spec.Description -Force | Out-Null

        Write-Host "registered $($spec.Name)  (every $($spec.IntervalMin) min within the band)"
    }

    # Two settings are deliberately the opposite of the collectors'.
    #
    # No WakeToRun: waking a sleeping machine in order to put it to sleep is absurd, and
    # would burn a wake cycle every day for nothing.
    #
    # No StartWhenAvailable: a missed run must NOT fire late. If the machine was off at
    # 11:05 and boots at 18:00, StartWhenAvailable would run this task minutes after the
    # user sits down and suspend the machine underneath them.
    $sleepAction = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument ("-NoProfile -NonInteractive -WindowStyle Hidden " +
                   "-ExecutionPolicy Bypass -File `"$ThisScript`" -SleepIfIdle") `
        -WorkingDirectory $RepoRoot
    $sleepSettings = New-ScheduledTaskSettingsSet `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
        -DontStopOnIdleEnd `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    $sleepSettings.WakeToRun = $false
    $sleepSettings.StartWhenAvailable = $false

    Register-ScheduledTask -TaskName $SleepTaskName `
        -Action $sleepAction `
        -Trigger (New-ScheduledTaskTrigger -Daily -At $SleepAt) `
        -Settings $sleepSettings `
        -Description 'Prediction market validation: sleep after the band, unless in use' `
        -Force | Out-Null
    Write-Host "registered $SleepTaskName  (at $SleepAt, skips if the machine is in use)"

    Write-Host ""
    Write-Host "Band: $BandStart-$BandEnd local, daily. Wakes the machine from sleep."
    Write-Host "Logs: $LogDir\<job>-<date>.log"
    Write-Host "Wake-to-run works from SLEEP, not from a full shutdown. If you shut the"
    Write-Host "machine down at night, enable wake-on-RTC in the BIOS or leave it asleep."
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
    if (Get-ScheduledTask -TaskName $SleepTaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $SleepTaskName -Confirm:$false
        Write-Host "removed $SleepTaskName"
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
        # A running job is not a failing one: LastTaskResult then describes the previous
        # run, and after a Stop/Start cycle it holds the stop's error code. Flagging that
        # puts a permanent FAILING next to the healthiest job on the list, which is how a
        # warning stops being read.
        $rc = $info.LastTaskResult
        $healthy = $rc -eq 0 -or $rc -eq 267009 -or $rc -eq 267011 -or $task.State -eq 'Running'
        $verdict = if ($healthy) { "" } else { "   <-- FAILING (last scheduled run)" }
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
    'Run'         { Invoke-Job -JobName $Run }
    'SleepIfIdle' { Invoke-SleepIfIdle -IdleMinutes $IdleMinutes }
    'Install'     { Install-Tasks }
    'Uninstall'   { Uninstall-Tasks }
    default       { Show-Status }
}
