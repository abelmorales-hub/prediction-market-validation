# Daily band watchdog, run by the `pmv-band-check` scheduled task after the band closes.
#
# This replaces the `watchdog` job that lived in .github/workflows/feeds.yml. That one had
# two faults and the second is the fatal one:
#
#   1. It judged GitHub's evidence -- `gh run list --workflow band.yml --status success` --
#      so a healthy desktop band with a dead Actions cron read as "no band today". The
#      collector that actually carries the study was invisible to its own watchdog.
#   2. It rode the hourly `5 * * * *` cron because that was "the only trigger known to
#      fire". On 2026-10-12 that stopped being true: zero scheduled runs in six hours
#      while a manual dispatch of the same file succeeded in 20s. A watchdog on an
#      unreliable trigger reports nothing on exactly the days worth reporting.
#
# Task Scheduler is the trigger that demonstrably fires on this machine -- it ran the
# whole band today -- so the watchdog now lives next to the thing it watches.
#
# The exit code IS the verdict, same contract as g0_check.ps1: 0 alive, 1 band lost, 2
# misconfigured. `Get-ScheduledTaskInfo -TaskName pmv-band-check` answers it without
# opening the log.
#
# WHAT THIS DOES NOT ANSWER. It is not G0 and must never be read as G0. It asks only
# whether the last closed band captured anything at all. A band that ran and left holes
# around a prediction horizon passes here and fails `pmv-g0-check`, which is the point:
# this alarm has to stay quiet on healthy days or it stops being read. Amendment 10's
# ledger is the shared primitive; `--liveness` reads `.clean`, G0 reads gap severity.
#
# -Since is today's LOCAL midnight, not the G0 clock start, and that difference is
# deliberate -- this is a daily liveness question, so yesterday's dead morning must not
# keep it red today. `band_days` only judges whole closed bands, so a run before 11:30
# prints "no closed band in range yet" and exits 0 rather than crying about a band still
# in progress.
param(
    # Pinned to match the collector's own band. supervisor.ps1 derives its tasks from
    # $BandStart/$BandDurationH; if those move, this moves with them or the watchdog
    # starts judging a window the collector never ran in.
    [string]$Band = '05:30-11:30',
    [string]$Tz   = 'Europe/Madrid',
    # Best-effort desktop toast on a lost band. Off by default so the task's own
    # invocation decides, and so running this by hand never pops a notification.
    [switch]$Notify
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$py   = Join-Path $repo '.venv\Scripts\python.exe'
$log  = Join-Path $repo ('logs\band-{0}.log' -f (Get-Date -Format 'yyyyMMdd'))

if (-not (Test-Path $py)) { throw "venv python not found at $py -- run uv sync first" }

# gap_report resolves `scripts.gap_report` off the working directory, not a PYTHONPATH.
Set-Location $repo

# Local midnight WITH its offset. A bare timestamp is read as UTC by parse_since, which
# across a +02:00 offset would start the range two hours into the previous local day and
# pull yesterday's band-day into the ledger -- making `ledger[-1]` yesterday, not today.
$since = (Get-Date).Date.ToString('yyyy-MM-ddTHH:mm:sszzz')

('=== band check {0} (since {1}, band {2} {3}) ===' -f
    (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $since, $Band, $Tz) |
    Out-File -Append -Encoding utf8 $log

$out = & $py -m scripts.gap_report --liveness --since $since --band $Band --tz $Tz 2>&1
$code = $LASTEXITCODE
$out | Out-File -Append -Encoding utf8 $log

if ($code -ne 0 -and $Notify) {
    # Toast via WinRT, no module to install. Whether it actually displayed is logged
    # either way: a notifier that fails silently would rebuild the exact bug this script
    # exists to remove, so the log says which channel carried the alarm.
    try {
        [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
        $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
            [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $texts = $xml.GetElementsByTagName('text')
        $texts.Item(0).AppendChild($xml.CreateTextNode('pmv: band lost')) | Out-Null
        $texts.Item(1).AppendChild($xml.CreateTextNode(
            [string]($out | Select-Object -Last 1))) | Out-Null
        # PowerShell's own AppUserModelId: it is already registered on the machine, so the
        # toast is not silently dropped for an unknown AUMID.
        $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(
            '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe')
        $notifier.Show([Windows.UI.Notifications.ToastNotification]::new($xml))
        "  notified: toast shown" | Out-File -Append -Encoding utf8 $log
    }
    catch {
        "  NOTIFY FAILED: $($_.Exception.Message) -- verdict is still in the exit code" |
            Out-File -Append -Encoding utf8 $log
    }
}

exit $code
