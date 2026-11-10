#Requires -Version 5.1
<#
.SYNOPSIS
    One-command local acceptance stack: migrate + seed + admin + API + frontend dev server.

.DESCRIPTION
    Prepares the runtime needed by G4 security / E2E checks on a local SQLite DB.
    - Uses the project .venv created by scripts/verify_all.ps1
    - API: http://127.0.0.1:<ApiPort>   (default 8000, /api prefix)
    - Web: http://127.0.0.1:<WebPort>   (default 5173, Vite proxies /api -> API)
    - PIDs are written to .run/, logs to reports/stack/; stop with scripts/stop_stack.ps1
    - Default accounts (matching the local security suite):
        qa-admin / demo-teacher / demo-student-1 / demo-student-2
        password: env EXAM_QA_PASSWORD, default dev-demo-password-123 (LOCAL ONLY)

    NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less files as
    ANSI; non-ASCII bytes can swallow braces/quotes and produce parser errors.

.EXAMPLE
    scripts\run_stack.cmd
.EXAMPLE
    scripts\run_stack.cmd -ResetDb -ApiPort 8001 -WebPort 5174
#>
[CmdletBinding()]
param(
    [int]$ApiPort = 8000,
    [int]$WebPort = 5173,
    [switch]$ResetDb,
    [switch]$SkipSeed,
    [switch]$NoFrontend
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$runDir = Join-Path $root '.run'
$logDir = Join-Path $root 'reports\stack'
New-Item -ItemType Directory -Force -Path $runDir, $logDir | Out-Null

# ---- Python: must be the project venv created by verify_all ----
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    throw 'No .venv found. Run scripts\verify_all.cmd first (it creates .venv and installs deps).'
}
$py = $venvPython

# ---- Node: real system node first, DSH-bundled as fallback ----
$runtimeBase = Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies'
$nodeCandidates = @()
$systemNode = Get-Command node -ErrorAction SilentlyContinue | Select-Object -First 1
if ($systemNode -and $systemNode.Source -and $systemNode.Source -notmatch '\\WindowsApps\\') {
    $nodeCandidates += $systemNode.Source
}
$nodeCandidates += (Join-Path $runtimeBase 'node\bin\node.exe')
$node = $nodeCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

# ---- Runtime env for app + CLI ----
$env:DATABASE_URL = 'sqlite:///./dev.db'
$env:SESSION_COOKIE_SECURE = 'false'
$env:APP_ENV = 'local'
# Force Python UTF-8 mode. On zh-CN Windows the default locale encoding is GBK;
# alembic's configparser reads alembic.ini with encoding='locale' and would fail
# with UnicodeDecodeError if the file ever contains non-ASCII bytes.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
if (-not $env:EXAM_QA_PASSWORD) { $env:EXAM_QA_PASSWORD = 'dev-demo-password-123' }

function Wait-Http {
    param([string]$Url, [int]$TimeoutSec = 90)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) { return $true }
        }
        catch {
            # keep polling
        }
        Start-Sleep -Milliseconds 750
    }
    return $false
}

# ---- 1) migrate + seed ----
$server = Join-Path $root 'server'
Push-Location $server
try {
    if ($ResetDb) {
        Write-Host '[stack] reset database (dev.db)'
        Remove-Item -Path 'dev.db', 'dev.db-wal', 'dev.db-shm' -ErrorAction SilentlyContinue
    }
    Write-Host '[stack] alembic upgrade head'
    & $py -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "alembic upgrade failed (exit $LASTEXITCODE)" }

    if (-not $SkipSeed) {
        # Order matters: seed-demo only runs on an empty users table, so it must
        # run BEFORE create-admin.
        Write-Host '[stack] seed-demo (idempotent; must run before creating admin)'
        & $py -m app.cli seed-demo --password-env EXAM_QA_PASSWORD
        if ($LASTEXITCODE -ne 0) { throw "seed-demo failed (exit $LASTEXITCODE)" }

        Write-Host '[stack] create-admin qa-admin (exit 2 = already exists, tolerated)'
        & $py -m app.cli create-admin --username qa-admin --display-name QAAdmin --password-env EXAM_QA_PASSWORD
        $adminCode = $LASTEXITCODE
        if ($adminCode -ne 0 -and $adminCode -ne 2) { throw "create-admin failed (exit $adminCode)" }
    }

    Write-Host '[stack] self-check'
    & $py -m app.cli check
}
finally {
    Pop-Location
}

# ---- 2) start API ----
$failures = 0
Write-Host '[stack] starting API ...'
$apiOut = Join-Path $logDir 'api.log'
$apiErr = Join-Path $logDir 'api.err.log'
$apiArgs = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$ApiPort")
$apiProc = Start-Process -FilePath $py -ArgumentList $apiArgs -WorkingDirectory $server `
    -RedirectStandardOutput $apiOut -RedirectStandardError $apiErr -WindowStyle Hidden -PassThru
Set-Content -Path (Join-Path $runDir 'api.pid') -Value $apiProc.Id -Encoding ASCII
Write-Host ("[stack] API pid={0} log={1}" -f $apiProc.Id, $apiOut)

$apiHealthy = Wait-Http -Url ("http://127.0.0.1:{0}/api/healthz" -f $ApiPort) -TimeoutSec 90
if (-not $apiHealthy) {
    Write-Warning 'API health check timed out; see reports/stack/api.err.log'
    $failures++
}

# ---- 3) start frontend ----
$webProc = $null
if (-not $NoFrontend) {
    $web = Join-Path $root 'web'
    $viteJs = Join-Path $web 'node_modules\vite\bin\vite.js'
    if (-not $node) {
        Write-Warning 'node not found; skip frontend (run pnpm dev in web/ manually)'
    }
    elseif (-not (Test-Path $viteJs)) {
        Write-Warning 'web/node_modules/vite not found; skip frontend (run verify_all frontend first)'
    }
    else {
        Write-Host '[stack] starting frontend (vite) ...'
        $nodeDir = Split-Path -Parent $node
        $env:PATH = $nodeDir + ';' + $env:PATH
        # Keep Vite dev proxy target in sync with -ApiPort (see web/vite.config.ts)
        $env:API_PROXY_TARGET = "http://127.0.0.1:$ApiPort"
        $webOut = Join-Path $logDir 'web.log'
        $webErr = Join-Path $logDir 'web.err.log'
        # Start-Process joins -ArgumentList with spaces WITHOUT quoting, so a path
        # containing spaces (e.g. "Program Files (x86)") must be quoted manually.
        $viteArg = '"' + $viteJs + '"'
        $webProc = Start-Process -FilePath $node -ArgumentList @($viteArg, '--host', '127.0.0.1', '--port', "$WebPort") `
            -WorkingDirectory $web -RedirectStandardOutput $webOut -RedirectStandardError $webErr -WindowStyle Hidden -PassThru
        Set-Content -Path (Join-Path $runDir 'web.pid') -Value $webProc.Id -Encoding ASCII
        Write-Host ("[stack] web pid={0} log={1}" -f $webProc.Id, $webOut)
        $webHealthy = Wait-Http -Url ("http://127.0.0.1:{0}/" -f $WebPort) -TimeoutSec 90
        if (-not $webHealthy) {
            Write-Warning 'frontend health check timed out; see reports/stack/web.err.log'
            $failures++
        }
    }
}

# ---- 4) summary ----
Write-Host ''
if ($failures -eq 0) {
    Write-Host '================ STACK READY ================' -ForegroundColor Green
}
else {
    Write-Host '================ STACK PARTIAL ================' -ForegroundColor Yellow
}
Write-Host ("API : http://127.0.0.1:{0}/api   (healthz: /api/healthz)" -f $ApiPort)
if ($webProc) { Write-Host ("Web : http://127.0.0.1:{0}/" -f $WebPort) }
Write-Host ("logs: {0}" -f $logDir)
Write-Host ''
Write-Host 'Next (new terminal):' -ForegroundColor Cyan
Write-Host ("  scripts\verify_all.cmd -Only contract -WithSecurity -WithE2E -BaseUrl http://127.0.0.1:{0}" -f $WebPort)
Write-Host 'Stop:' -ForegroundColor Cyan
Write-Host '  scripts\stop_stack.cmd'
if ($failures -gt 0) {
    Write-Host ''
    Write-Host 'Some services failed to start. Check reports/stack/*.err.log' -ForegroundColor Red
    exit 1
}
exit 0
