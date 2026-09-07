# Daily G0 gate check, run by the `pmv-g0-check` scheduled task after the band closes.
#
# The exit code IS the verdict -- 0 clean, 1 CRITICAL -- so Get-ScheduledTaskInfo answers
# the gate without opening the log.
#
# -Since is the G0 CLOCK start, not the analysis window start. They are different dates
# and confusing them makes this gate useless in either direction: run from the window
# start (2026-09-03T18:33Z) and the 2026-09-04 stoppage makes it report NOT MET forever;
# move it forward without a fault to justify it and the gate passes on a short window.
# It moves only when a CRITICAL gap restarts the clock, and that belongs in an amendment.
#
# The band must be passed too. Without it every overnight hole counts and the gate can
# never pass; see docs/pre_declaration.md, Amendments 3, 4 and 8.
param(
    [string]$Since = '2026-09-05T00:00:00Z',
    [string]$Band  = '05:30-11:30',
    [string]$Tz    = 'Europe/Madrid'
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$py   = Join-Path $repo '.venv\Scripts\python.exe'
$log  = Join-Path $repo ('logs\g0-{0}.log' -f (Get-Date -Format 'yyyyMMdd'))

if (-not (Test-Path $py)) { throw "venv python not found at $py -- run uv sync first" }

# gap_report resolves `scripts.gap_report` off the working directory, not a PYTHONPATH.
Set-Location $repo

('=== G0 check {0} (since {1}, band {2}) ===' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Since, $Band) |
    Out-File -Append -Encoding utf8 $log

& $py -m scripts.gap_report --since $Since --band $Band --tz $Tz |
    Out-File -Append -Encoding utf8 $log

exit $LASTEXITCODE
