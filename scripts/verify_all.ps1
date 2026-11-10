#Requires -Version 5.1
<#
.SYNOPSIS
    One-shot acceptance pre-check for the MCQ exam system (G4 evidence helper).

.DESCRIPTION
    Steps: contract validation -> backend (ruff/mypy/pytest) -> frontend (lint/tsc/test/build).
    Optional: security, E2E, perf. Each step gets its own log; failed-step count is the exit code.

    Notes:
    - All user-facing messages are ASCII to stay readable on Windows PowerShell 5.1
      (which reads BOM-less UTF-8 files as ANSI and would garble Chinese text).
    - Prefer running via pwsh 7. If only PowerShell 5.1 is available, use scripts/verify_all.cmd.
    - Runtime resolution prefers a real system Python/Node on PATH (e.g. official
      Python 3.12 + Node 20). Python dependencies are installed into a project-local
      .venv so the system interpreter is not modified. The DSH-bundled runtimes are
      used only when no real Python/Node is available (Windows Store alias machines).
    - pip uses a mirror by default (see -PipIndexUrl) because direct PyPI downloads
      can stall for minutes on some networks. Every step has a hard timeout
      (-StepTimeoutSec) so a hung download can never freeze the whole run.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\verify_all.ps1
.EXAMPLE
    .\scripts\verify_all.ps1 -WithSecurity -ApiUrl http://127.0.0.1:8000/api
.EXAMPLE
    .\scripts\verify_all.ps1 -Only contract
.EXAMPLE
    .\scripts\verify_all.ps1 -PipIndexUrl https://pypi.org/simple -NpmRegistry https://registry.npmjs.org
#>
[CmdletBinding()]
param(
    [ValidateSet('all', 'contract', 'backend', 'frontend')]
    [string]$Only = 'all',
    [switch]$WithSecurity,
    [switch]$WithE2E,
    [switch]$WithPerf,
    [string]$ApiUrl = 'http://127.0.0.1:8000/api',
    [string]$BaseUrl = 'http://127.0.0.1:5173',
    [string]$PipIndexUrl = 'https://pypi.tuna.tsinghua.edu.cn/simple',
    [string]$NpmRegistry = 'https://registry.npmmirror.com',
    [string]$PythonExe = '',
    [string]$NodeExe = '',
    [string]$PnpmScript = '',
    [int]$StepTimeoutSec = 600,
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logDir = Join-Path $root "reports/verify-$stamp"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$script:results = @()

# Merge machine/user PATH from the registry *in front of* the inherited PATH so
# tools installed after the current shell was started (e.g. a fresh Python/Node
# install) are found, and their entries win over any Windows Store alias.
$registryPath = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
if ($registryPath.Trim(';')) { $env:PATH = $registryPath + ';' + $env:PATH }

# ---------------------------------------------------------------------------
# Runtime resolution.
# ---------------------------------------------------------------------------
$runtimeBase = Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies'
$bundledPython = Join-Path $runtimeBase 'python\python.exe'
$bundledNode = Join-Path $runtimeBase 'node\bin\node.exe'
$bundledPnpm = Join-Path $runtimeBase 'pnpm\bin\pnpm.mjs'

function Get-RealTool {
    # Return the source path of a real executable on PATH, or $null.
    # Windows Store aliases live in WindowsApps and fail with
    # "The system cannot execute the specified program." (launch failure).
    param([string]$Name)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd -or -not $cmd.Source) { return $null }
    if ($cmd.Source -match '\\WindowsApps\\') { return $null }
    return $cmd.Source
}

# ---- Python: real system Python -> project venv; otherwise DSH-bundled ----
$systemPython = $null
if (-not $PythonExe -or $PythonExe -eq 'python') { $systemPython = Get-RealTool 'python' }

if ($PythonExe -and $PythonExe -ne 'python') {
    # Explicit -PythonExe wins (no venv wrapping).
} elseif ($systemPython) {
    $venvDir = Join-Path $root '.venv'
    $venvPython = Join-Path $venvDir 'Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        Write-Host ("creating project venv: {0}" -f $venvDir)
        & $systemPython -m venv $venvDir
    }
    if (Test-Path $venvPython) {
        $PythonExe = $venvPython
        $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
    } else {
        Write-Warning 'venv creation failed; falling back to the system interpreter.'
        $PythonExe = $systemPython
    }
} elseif (Test-Path $bundledPython) {
    $PythonExe = $bundledPython
} else {
    $PythonExe = 'python'
}

# ---- Node: real system Node -> DSH-bundled ----
if (-not $NodeExe) {
    $systemNode = Get-RealTool 'node'
    if ($systemNode) { $NodeExe = $systemNode }
    elseif (Test-Path $bundledNode) { $NodeExe = $bundledNode }
    else { $NodeExe = 'node' }
}

# ---- pnpm: real global pnpm on PATH -> else bundled pnpm.mjs run by Node ----
if (-not $PnpmScript) {
    $systemPnpm = Get-RealTool 'pnpm'
    if (-not $systemPnpm -and (Test-Path $bundledPnpm)) { $PnpmScript = $bundledPnpm }
}

$pathParts = @()
# Python: its own folder first (contains python.exe), plus its Scripts folder,
# where pip installs console scripts (ruff/mypy/pytest/openapi-spec-validator/
# playwright). For a venv these are the same folder.
$pyDir = Split-Path -Parent $PythonExe
if (Test-Path $pyDir) { $pathParts += $pyDir }
$pyScripts = Join-Path $pyDir 'Scripts'
if (Test-Path $pyScripts) { $pathParts += $pyScripts }
# Node: node.exe folder (also contains npm.cmd / npx.cmd / corepack.cmd).
$nodeDir = Split-Path -Parent $NodeExe
if (Test-Path $nodeDir) { $pathParts += $nodeDir }
# pnpm: when only the bundled pnpm.mjs is available, run it through Node with a wrapper.
if ($PnpmScript) {
    $shimDir = Join-Path $logDir '_shim'
    New-Item -ItemType Directory -Force -Path $shimDir | Out-Null
    $pnpmCmd = Join-Path $shimDir 'pnpm.cmd'
    $pnpmBody = '@echo off' + "`r`n" + '"' + $NodeExe + '" "' + $PnpmScript + '" %*'
    # Default (ANSI) encoding: cmd.exe cannot read UTF-8/BOM batch files, and paths
    # here may legitimately contain non-ASCII characters (e.g. Chinese folders).
    Set-Content -Path $pnpmCmd -Value $pnpmBody -Encoding Default
    $pathParts += $shimDir
}
if ($pathParts.Count -gt 0) {
    $env:PATH = (($pathParts | Where-Object { $_ }) -join ';') + ';' + $env:PATH
}

# Registry for pnpm/npm steps (can be disabled by passing '').
if ($NpmRegistry) { $env:npm_config_registry = $NpmRegistry }

Write-Host ("python : {0}" -f $PythonExe)
Write-Host ("node   : {0}" -f $NodeExe)
Write-Host ("pnpm   : {0}" -f $(if ($PnpmScript) { 'bundled pnpm.mjs (via node)' } else { 'pnpm (from PATH)' }))
Write-Host ("pip    : {0}" -f $(if ($PipIndexUrl) { $PipIndexUrl } else { 'default PyPI' }))
Write-Host ("npm    : {0}" -f $(if ($NpmRegistry) { $NpmRegistry } else { 'default npmjs' }))
Write-Host ("timeout: {0}s per step" -f $StepTimeoutSec)

# ---------------------------------------------------------------------------
# Step runner with a hard timeout so a stalled download cannot hang the run.
# ---------------------------------------------------------------------------
function Invoke-Step {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Dir,
        [Parameter(Mandatory)][string]$Command
    )
    $index = $script:results.Count + 1
    $safe = ($Name -replace '[^\w\-]+', '_')
    $log = Join-Path $logDir ('{0:00}-{1}.log' -f $index, $safe)
    Write-Host ''
    Write-Host ("=== [{0}] {1} ===" -f $index, $Name) -ForegroundColor Cyan
    Write-Host ("    dir : {0}" -f $Dir)
    Write-Host ("    cmd : {0}" -f $Command)
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $code = 1

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $env:ComSpec
    $psi.Arguments = '/d /s /c "' + $Command + '"'
    $psi.WorkingDirectory = $Dir
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    try {
        $proc = [System.Diagnostics.Process]::Start($psi)
        $outTask = $proc.StandardOutput.ReadToEndAsync()
        $errTask = $proc.StandardError.ReadToEndAsync()
        if ($proc.WaitForExit($StepTimeoutSec * 1000)) {
            $code = $proc.ExitCode
        } else {
            try { $proc.Kill() } catch { }
            $code = 124
            Add-Content -Path $log -Value ("[verify_all] TIMEOUT: step killed after {0}s" -f $StepTimeoutSec) -Encoding UTF8
        }
        $text = $outTask.Result + $errTask.Result
        if ($text) { $text | Tee-Object -FilePath $log -Append | Out-Host }
    } catch {
        ($_ | Out-String) | Tee-Object -FilePath $log -Append | Out-Host
        $code = 1
    } finally {
        $watch.Stop()
    }

    $script:results += [pscustomobject]@{
        Step     = $Name
        ExitCode = $code
        Seconds  = [math]::Round($watch.Elapsed.TotalSeconds, 1)
        Log      = $log
    }
    if ($code -ne 0) {
        Write-Host ("    -> FAIL (exit {0})" -f $code) -ForegroundColor Red
    }
}

$py = 'python'
# --no-cache-dir: a stale pip HTTP-cache lock (e.g. left behind by a killed run)
# makes pip hang forever on "Looking in indexes". Disabling the cache keeps the
# pre-check deterministic; downloads are cheap over a mirror.
$pipExtra = ' --no-cache-dir --timeout 60 --retries 5 --disable-pip-version-check'
if ($PipIndexUrl) { $pipExtra += ' -i ' + $PipIndexUrl }

Invoke-Step 'preflight-tools' $root 'python --version & node --version & pnpm --version'

if ($Only -eq 'all' -or $Only -eq 'contract') {
    if (-not $SkipInstall) {
        Invoke-Step 'contract-deps' $root "$py -m pip install -q -r tests/requirements.txt$pipExtra"
    }
    Invoke-Step 'openapi-validate' $root "$py -m openapi_spec_validator contracts/openapi.yaml"
    Invoke-Step 'contract-tests' $root "$py -m pytest tests/contract -q"
}

if ($Only -eq 'all' -or $Only -eq 'backend') {
    if (-not $SkipInstall) {
        Invoke-Step 'backend-deps' $root "$py -m pip install -q -e server[dev]$pipExtra"
    }
    $server = Join-Path $root 'server'
    Invoke-Step 'backend-ruff' $server "$py -m ruff check ."
    Invoke-Step 'backend-mypy' $server "$py -m mypy app"
    Invoke-Step 'backend-pytest' $server "$py -m pytest -q"
}

if ($Only -eq 'all' -or $Only -eq 'frontend') {
    $web = Join-Path $root 'web'
    if (-not $SkipInstall) {
        Invoke-Step 'frontend-deps' $web 'pnpm install --no-frozen-lockfile'
        Invoke-Step 'frontend-gen-types' $web 'pnpm gen:types'
    }
    Invoke-Step 'frontend-lint' $web 'pnpm lint'
    Invoke-Step 'frontend-tsc' $web 'pnpm tsc --noEmit'
    Invoke-Step 'frontend-test' $web 'pnpm test'
    Invoke-Step 'frontend-build' $web 'pnpm build'
}

if ($WithSecurity) {
    $env:EXAM_API_URL = $ApiUrl
    Invoke-Step 'security-tests' $root "$py -m pytest tests/security -q"
    Remove-Item Env:EXAM_API_URL -ErrorAction SilentlyContinue
}

if ($WithE2E) {
    if (-not $SkipInstall) {
        Invoke-Step 'playwright-install' $root "$py -m playwright install chromium"
    }
    $env:EXAM_E2E = '1'
    $env:EXAM_BASE_URL = $BaseUrl
    Invoke-Step 'e2e-tests' $root "$py -m pytest tests/e2e -q"
    Remove-Item Env:EXAM_E2E, Env:EXAM_BASE_URL -ErrorAction SilentlyContinue
}

if ($WithPerf) {
    Invoke-Step 'perf-k6' $root 'k6 run tests/perf/k6/exam_flow.js'
}

Write-Host ''
Write-Host '================ SUMMARY ================' -ForegroundColor Yellow
$script:results | Format-Table Step, ExitCode, Seconds, Log -AutoSize
Write-Host ("log dir: {0}" -f $logDir)

$failed = @($script:results | Where-Object { $_.ExitCode -ne 0 }).Count
if ($failed -gt 0) {
    Write-Host ("RESULT: FAILED ({0} step(s))" -f $failed) -ForegroundColor Red
    exit $failed
}
Write-Host 'RESULT: PASS (all steps green)' -ForegroundColor Green
exit 0
