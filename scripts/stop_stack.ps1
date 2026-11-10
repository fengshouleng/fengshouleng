#Requires -Version 5.1
<#
.SYNOPSIS
    Stop the local acceptance stack started by scripts/run_stack.ps1 (API + frontend).

.DESCRIPTION
    Reads .run/api.pid and .run/web.pid, kills those processes, and removes the pid files.
    Already-exited processes are skipped silently. Other processes are not touched.

    NOTE: keep this file ASCII-only (Windows PowerShell 5.1 reads BOM-less files as ANSI).
#>
[CmdletBinding()]
param()

$root = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $root '.run'

function Stop-FromPidFile {
    param([string]$Name)
    $pidFile = Join-Path $runDir ("{0}.pid" -f $Name)
    if (-not (Test-Path $pidFile)) {
        Write-Host ("[stop] {0}: no pid file" -f $Name)
        return
    }
    $raw = (Get-Content -Path $pidFile -ErrorAction SilentlyContinue) -join ''
    $processId = 0
    if (-not [int]::TryParse($raw.Trim(), [ref]$processId)) {
        Write-Warning ("[stop] {0}: invalid pid file" -f $Name)
        Remove-Item -Path $pidFile -ErrorAction SilentlyContinue
        return
    }
    $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($proc) {
        try {
            Stop-Process -Id $processId -Force -ErrorAction Stop
            Write-Host ("[stop] {0}: killed pid {1}" -f $Name, $processId)
        }
        catch {
            Write-Warning ("[stop] {0}: failed to kill pid {1}: {2}" -f $Name, $processId, $_)
        }
    }
    else {
        Write-Host ("[stop] {0}: pid {1} already exited" -f $Name, $processId)
    }
    Remove-Item -Path $pidFile -ErrorAction SilentlyContinue
}

Stop-FromPidFile -Name 'web'
Stop-FromPidFile -Name 'api'
Write-Host '[stop] done'
