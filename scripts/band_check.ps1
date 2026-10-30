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
    #
    # Moved 05:30 -> 04:30 on 2026-10-14 with the band itself (Amendment 17), then to UTC
    # on 2026-10-30. The local-time version carried this reasoning: "liveness is the one
    # question here that must be asked in *local* time: the collector is scheduled by a
    # local wall clock, so a watchdog pinned to UTC would cry BAND LOST on the DST seam for
    # a band that ran exactly as configured." The premise was false, and it is worth being
    # precise about why, because the same assumption sits in supervisor.ps1's own comments.
    #
    # `New-ScheduledTaskTrigger -Daily -At '04:30'` does not store "04:30, local, daily".
    # It serialises to a StartBoundary carrying an explicit offset -- pmv-snapshot's reads
    # `2026-10-21T03:30:00+01:00`, written with the machine's *standard* offset even though
    # 10-21 was CEST -- and Task Scheduler honours that offset as an absolute instant
    # rather than re-reading the wall clock each morning. So the collectors are pinned to
    # 02:30Z and have never moved. What moved was this watchdog's question.
    #
    # From 2026-10-25, when Madrid left DST, the task kept firing at 09:33Z (10:33 local,
    # down from 11:33) while still asking about 04:30-11:30 *local* -- a window that now
    # closes at 10:30Z, an hour after the check runs. So it printed "no closed band in
    # range yet; not judging" and exited 0 every day from 10-25 on, including 2026-10-29,
    # a total local loss (the 02:30Z wake timer did not fire, then the unarmed-repetition
    # trap finished it) that the toast never reported. Seventh time a gate here has
    # answered an easier question than the one declared, and the first where DST, not a
    # code change, did the swapping.
    #
    # Stating the band in UTC pins it to the same clock the trigger actually uses, so the
    # seam cannot separate the two again.
    #
    # THESE DEFAULTS ARE THE FALLBACK FOR A HAND RUN, NOT THE PRODUCTION CONFIGURATION.
    # Since 2026-10-30 `supervisor.ps1 -Install` registers the task with `-Band` and `-Tz`
    # passed explicitly, so the window the watchdog asks about is written by the same code
    # that writes the trigger it fires from, and Install-Tasks throws if the check does not
    # land after the close. They are kept equal to supervisor.ps1's $BandStartUtc and
    # $BandEndUtc anyway: a hand run that silently judged a different window than the task
    # does would be its own trap.
    #
    # The band was 02:30-09:30Z for nine days and is 03:30-10:30Z from the next -Install.
    # That is not a correction of Amendment 17 but a delivery of it: the UTC band was
    # always meant to walk an hour later when Madrid left DST, and it did not, because
    # `-At` had frozen the CEST instant. The frozen 02:30Z start was harmless -- early, not
    # late -- but 09:30Z stops covering the T-24h instants on 2026-11-01, when US DST ends
    # and they move to 05:00-10:00Z. See supervisor.ps1's band block for the measurement.
    #
    # Note this is the collectors' repetition window, which the snapshotter's own `--until`
    # now matches exactly; before 10-30 it outlived the band by an hour under CET.
    [string]$Band = '03:30-10:30',
    [string]$Tz   = 'UTC',
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
