# The single G3 run, per docs/pre_declaration.md Amendment 11. Run by the `pmv-g3` task.
#
# Register it (and this is recorded here on purpose -- `pmv-g0-check` was registered by
# hand, appeared nowhere in the repo, and rotted twice because of it):
#
#     .\scripts\g3_run.ps1 -Install
#     .\scripts\g3_run.ps1 -Uninstall
#
# Three things make an unattended run of an irreversible act safe:
#
#   * `--require-n` refuses *before* `build_report`, which is where a score first exists,
#     so an early firing spends nothing;
#   * the report file is the record -- if it exists the look is already spent and this
#     script will not produce a second one;
#   * the task unregisters itself after the run that succeeds, so the hourly retries
#     cannot turn one look into a daily series at a growing n.
#
# Amendment 11 also declares the fallback: if the frozen sample has not reached n by the
# deadline, the run happens anyway on whatever resolved, with the shortfall stated. That
# is implemented here rather than left to someone remembering it -- past the deadline the
# guard is dropped and the run goes ahead.
param(
    [switch]$Install,
    [switch]$Uninstall,
    [int]$RequireN = 715,
    [string]$Deadline = '2026-10-11T00:00:00Z',
    [string]$FirstRun = '2026-10-10 06:15',
    [string]$TaskName = 'pmv-g3'
)

$ErrorActionPreference = 'Stop'
$ThisScript = $PSCommandPath
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo '.venv\Scripts\python.exe'
$log = Join-Path $repo ('logs\g3-{0}.log' -f (Get-Date -Format 'yyyyMMdd'))
$report = Join-Path $repo 'reports\g3_h2.md'

function Write-Log([string]$Message) {
    ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message) |
        Out-File -Append -Encoding utf8 $log
}

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "unregistered $TaskName"
    } else {
        Write-Host "$TaskName is not registered"
    }
    exit 0
}

if ($Install) {
    $at = [datetime]::ParseExact($FirstRun, 'yyyy-MM-dd HH:mm', [cultureinfo]::InvariantCulture)

    # -WindowStyle Hidden for the reason supervisor.ps1 documents: without it the job
    # attaches to whatever console started it and dies with STATUS_CONTROL_C_EXIT when
    # that shell exits, logging nothing.
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument ("-NoProfile -NonInteractive -WindowStyle Hidden " +
                   "-ExecutionPolicy Bypass -File `"$ThisScript`"") `
        -WorkingDirectory $repo

    # Daily from the first run, retried hourly inside the band. Retries exist because the
    # 05:30 resolution sweep is not guaranteed to have caught every settlement; they are
    # harmless because a short sample refuses and a spent look refuses.
    $trigger = New-ScheduledTaskTrigger -Daily -At $at
    $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $at `
        -RepetitionInterval (New-TimeSpan -Hours 1) `
        -RepetitionDuration (New-TimeSpan -Hours 5)).Repetition

    $settings = New-ScheduledTaskSettingsSet `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
        -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    # Off for the same reason as the collectors': a missed occurrence caught up "as soon
    # as possible" plus WakeToRun is how this machine ended up awake all night on
    # 2026-09-03. There is no value in a late run -- the next day's trigger is fine.
    $settings.StartWhenAvailable = $false
    # The band wakes the machine at 05:30 anyway; this is belt and braces for a day the
    # collectors do not run.
    $settings.WakeToRun = $true

    # No EndBoundary. A hardcoded end date is the rot mechanism that silently stopped
    # `pmv-g0-check` for two days; this task ends by retiring itself after it succeeds,
    # and past the deadline it always succeeds.
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Force `
        -Description 'Single pre-committed G3 run (pre_declaration Amendment 11). Retires itself.' |
        Out-Null
    Write-Host "registered $TaskName  (first run $FirstRun, hourly for 5h, retires on success)"
    exit 0
}

if (-not (Test-Path $py)) { throw "venv python not found at $py -- run uv sync first" }
Set-Location $repo

if (Test-Path $report) {
    Write-Log "SKIP: $report already exists -- the single look is spent. Retiring."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    exit 0
}

$deadlineUtc = [datetime]::Parse($Deadline, [cultureinfo]::InvariantCulture,
    [System.Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()

# The command is Amendment 11's, verbatim. It is not built from variables the caller can
# retune: a fixed sample is only fixed if the command that reads it is.
$argv = @(
    '-m', 'scripts.score',
    '--model', 'gefs_ensemble_raw',
    '--horizon', 'T-24h',
    '--since', '2026-09-20T00:00:00Z',
    '--until', '2026-10-08T09:30:00Z',
    '--exclude', 'docs/g3_excluded_markets.txt',
    '--out', 'reports/g3_h2.md'
)
if ([datetime]::UtcNow -lt $deadlineUtc) {
    $argv += @('--require-n', "$RequireN")
    Write-Log "=== G3 attempt (guarded, --require-n $RequireN) ==="
} else {
    Write-Log "=== G3 attempt (FALLBACK: past $Deadline, guard dropped per Amendment 11) ==="
}

& $py @argv | Out-File -Append -Encoding utf8 $log
$code = $LASTEXITCODE

if ($code -eq 0) {
    # `reports/` is gitignored, on the reasoning that a committed report is a claim about
    # a sample size that has since moved. That reasoning does not hold for this one: its
    # sample is frozen by declaration, it happens once, and it is the result of record.
    # Copied into a tracked path so committing it is all that is left to do by hand.
    Copy-Item -Path $report -Destination (Join-Path $repo 'docs\g3_result.md') -Force
    Write-Log "RAN: $report, copied to docs\g3_result.md -- commit it. Retiring $TaskName."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
} else {
    Write-Log "refused or failed (exit $code). Nothing scored; will retry."
}
exit $code
