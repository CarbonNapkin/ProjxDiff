<#
.SYNOPSIS
    Point the "ProjxDiff Nightly Sync" scheduled task at the copy of the app
    that was just installed. Run by the installer (see ProjxDiff.iss).

.DESCRIPTION
    An upgrade replaces the files on disk and never looks at Task Scheduler,
    so a site whose task points somewhere the upgrade did not touch upgrades
    "successfully" and keeps running the old binary at 02:00 with no error and
    no sign anything is wrong. Nothing about a nightly task failing is loud.

    This is deliberately narrow:

      * A machine with no such task is left alone. Absent a task there is no
        config path to schedule, and silently creating a nightly job on a
        machine that never asked for one is not a repair.
      * Only the executable path is rewritten. Arguments (the config path),
        triggers, principal and settings are whatever the site already chose,
        and this has no business guessing at them.
      * A command that is not recognisably a Projx Diff executable is reported
        and left untouched. Something else owns that task; blindly rewriting
        production task definitions is what caused the 2026-08-16 incident in
        the first place.

    The installer runs elevated (it has to, to write Program Files), which is
    the one moment in the lifecycle where re-registering a SYSTEM task is
    possible without a separate UAC prompt.

.PARAMETER ExePath
    Full path to the freshly-installed ProjxDiff.exe.

.OUTPUTS
    Exit codes, consumed by the installer's [Code] section:
      0  nothing to do (no task, or it already points at ExePath)
      10 the task was repointed at ExePath
      11 the task exists but its command is not a Projx Diff executable
      12 the task exists and is stale, but rewriting it failed
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $ExePath
)

$ErrorActionPreference = 'Stop'
$TaskName = 'ProjxDiff Nightly Sync'

try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
} catch {
    $task = $null
}

if (-not $task) {
    Write-Output "No '$TaskName' task on this machine; nothing to verify."
    exit 0
}

# A task can hold several actions; only ones that execute something matter.
$actions = @($task.Actions | Where-Object { $_.Execute })
if ($actions.Count -eq 0) {
    Write-Output "Task '$TaskName' has no executable action; leaving it alone."
    exit 11
}

$current = $actions[0].Execute.Trim('"')
Write-Output "Task '$TaskName' currently runs: $current"
Write-Output "This install is at:              $ExePath"

if ($current -ieq $ExePath) {
    Write-Output 'Already pointing at this install; nothing to do.'
    exit 0
}

# "Is this ours?" by filename, not by path -- the whole point is that the path
# is wrong. Matches the _command_is_ours guard in dw_compare/gui.py, which
# exists for the same reason.
$leaf = try { [System.IO.Path]::GetFileName($current) } catch { '' }
if ($leaf -notmatch '^(?i)ProjxDiff(-cli)?\.exe$') {
    Write-Output "That command is not a Projx Diff executable. Leaving the task"
    Write-Output "untouched -- repair it from Tools > Manage Nightly Sync if it"
    Write-Output "should be running the nightly sync."
    exit 11
}

try {
    # Rewrite the path only. Arguments carry the site's config path and the
    # triggers carry its chosen schedule; both stay exactly as they are.
    $new = foreach ($a in $task.Actions) {
        if (-not $a.Execute) { $a; continue }
        $args = @{ Execute = $ExePath }
        if ($a.Arguments)        { $args['Argument'] = $a.Arguments }
        if ($a.WorkingDirectory) { $args['WorkingDirectory'] = $a.WorkingDirectory }
        New-ScheduledTaskAction @args
    }
    Set-ScheduledTask -TaskName $TaskName -TaskPath $task.TaskPath -Action $new | Out-Null
    Write-Output "Repointed '$TaskName' at $ExePath (schedule and arguments unchanged)."
    exit 10
} catch {
    Write-Output "Could not update the task: $($_.Exception.Message)"
    exit 12
}
