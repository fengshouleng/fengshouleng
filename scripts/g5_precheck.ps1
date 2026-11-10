# g5_precheck —— G5 上线验收预检：把「不依赖生产环境就能判」的项自动判掉。 #  # 覆盖 docs/73 §1/§2/§3/§4/§5 中**本地可验证**的部分（G4 结论、演练 JSON、备份/回滚判据、 # compose 配置、TLS 证书与 env 关键项、分支保护状态），输出 PASS/FAIL/SKIP 矩阵与证据 JSON。 # 真正的生产动作（DNS/证书签发/发布执行/24h 观察/签字）仍需人工，清单见 docs/78。 #  # 用法（仓库根）： #   pwsh -File scripts/g5_precheck.ps1 #   $env:G5_JSON = "reports/ci/g5-precheck.json"; pwsh -File scripts/g5_precheck.ps1 #  # 退出码：0=没有 FAIL（允许 SKIP）；1=存在 FAIL。


$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$results = @()

function Add-Result {
    param([string]$Id, [string]$Item, [string]$Status, [string]$Detail)
    $script:results += [pscustomobject]@{ id = $Id; item = $Item; status = $Status; detail = $Detail }
}

# --- 1. G4 报告结论 ---------------------------------------------------------
$gate = 'docs/61-gate-report.md'
if (Test-Path $gate) {
    $text = Get-Content $gate -Raw -Encoding UTF8
    $hasCond = $text -match 'G4\s*=\s*\*\*CONDITIONAL'
    $hasPass = $text -match 'G4\s*[:：]\s*PASS'
    if ($hasPass) { Add-Result '1.1' 'G4 报告结论 PASS' 'PASS' 'docs/61 含 G4: PASS' }
    elseif ($hasCond) { Add-Result '1.1' 'G4 报告结论 PASS' 'FAIL' 'G4 仍为 CONDITIONAL（性能待 ≥8 vCPU 销项，docs/76）' }
    else { Add-Result '1.1' 'G4 报告结论 PASS' 'SKIP' '未在 docs/61 找到 G4: PASS / CONDITIONAL 行' }
} else {
    Add-Result '1.1' 'G4 报告存在' 'FAIL' 'docs/61-gate-report.md 不存在'
}

# --- 2. 发布演练 JSON（1.3 / 5.3） -----------------------------------------
$drills = Get-ChildItem reports -Filter 'release-drill-*.json' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending
if ($drills) {
    $latest = $drills[0]
    try {
        $d = Get-Content $latest.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        $ok = ($d.result -eq 'PASS') -and ($d.rollback_within_15min -eq $true)
        $detail = "file=$($latest.Name) g4=$($d.g4) result=$($d.result) rollback_seconds=$($d.rollback_seconds) total_seconds=$($d.total_seconds)"
        Add-Result '1.3' '演练 JSON result=PASS 且回滚 < 15min' $(if ($ok) { 'PASS' } else { 'FAIL' }) $detail
    } catch {
        Add-Result '1.3' '演练 JSON 可解析' 'FAIL' "解析失败：$($_.Exception.Message)"
    }
} else {
    Add-Result '1.3' '演练 JSON 存在' 'FAIL' 'reports/release-drill-*.json 不存在（先跑 deploy/release/staging_drill.sh）'
}

# --- 3. compose 配置 -------------------------------------------------------
$composeOk = $null
try {
    $env:COMPOSE_PROJECT_NAME = 'exam-config-check'
    $out = & docker compose -f deploy/docker-compose.yml --env-file deploy/.env.example config -q 2>&1
    $composeOk = ($LASTEXITCODE -eq 0)
    $detailText = ($out | Select-Object -First 3) -join ' '
    Add-Result '3.6/§9' 'compose config -q' $(if ($composeOk) { 'PASS' } else { 'FAIL' }) $detailText
} catch {
    Add-Result '3.6/§9' 'compose config -q' 'SKIP' "docker 不可用：$($_.Exception.Message)"
}

# --- 4. TLS / 证书 / env 关键项（§4） ---------------------------------------
$certDir = 'deploy/certs'
if (Test-Path (Join-Path $certDir 'fullchain.pem')) {
    Add-Result '4.2' 'TLS 证书存在' 'PASS' 'deploy/certs/fullchain.pem 存在（有效期需人工 openssl 复核）'
} else {
    Add-Result '4.2' 'TLS 证书存在' 'SKIP' 'deploy/certs/fullchain.pem 不存在——生产部署前必须签发（docs/73 §4）'
}

$envProd = 'deploy/.env.prod'
if (Test-Path $envProd) {
    $envText = Get-Content $envProd -Raw -Encoding UTF8
    $secureOn = $envText -match '(?m)^SESSION_COOKIE_SECURE=true\s*$'
    Add-Result '4.5' 'SESSION_COOKIE_SECURE=true' $(if ($secureOn) { 'PASS' } else { 'FAIL' }) 'deploy/.env.prod'
    $weak = $envText -match 'CHANGE_ME|dev-demo-password'
    Add-Result '4.6' '生产 env 无占位/默认密码' $(if ($weak) { 'FAIL' } else { 'PASS' }) 'deploy/.env.prod'
} else {
    Add-Result '4.5' 'deploy/.env.prod 存在' 'SKIP' '尚无生产 env（按 docs/73 §4 从 .env.example 复制并填值）'
}

# --- 5. 分支保护（B/P-005，docs/77） --------------------------------------
$token = $env:GH_TOKEN
if ($token) {
    $headers = @{ Authorization = "Bearer $token"; 'User-Agent' = 'dsh-agent'; Accept = 'application/vnd.github+json' }
    try {
        $prot = Invoke-RestMethod -Uri 'https://api.github.com/repos/fengshouleng/fengshouleng/branches/main/protection' -Headers $headers -TimeoutSec 30
        $contexts = @($prot.required_status_checks.contexts) -join ','
        Add-Result 'P-005' 'main 分支保护（required ci-gate）' 'PASS' "contexts=$contexts enforce_admins=$($prot.enforce_admins.enabled)"
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        if ($code -eq 404) { Add-Result 'P-005' 'main 分支保护' 'FAIL' '未开启保护（按 docs/77 §3 开启）' }
        elseif ($code -eq 403) { Add-Result 'P-005' 'main 分支保护' 'FAIL' 'token 无 Administration 权限，需管理员执行（docs/77 §2）' }
        else { Add-Result 'P-005' 'main 分支保护' 'SKIP' "HTTP $code" }
    }
} else {
    Add-Result 'P-005' 'main 分支保护' 'SKIP' '未设置 GH_TOKEN'
}

# --- 6. 关键脚本存在性（§2/§5 可执行性） -----------------------------------
$required = @(
    'deploy/backup/backup.sh',
    'deploy/backup/restore_drill.sh',
    'deploy/release/deploy.sh',
    'deploy/release/rollback.sh',
    'deploy/release/staging_drill.sh',
    'deploy/monitoring/smoke_alert.sh'
)
$missing = $required | Where-Object { -not (Test-Path $_) }
Add-Result '§2/§5' '发布/备份/监控脚本存在' $(if ($missing) { 'FAIL' } else { 'PASS' }) ($(if ($missing) { '缺失: ' + ($missing -join ', ') } else { '全部存在' }))

# --- 输出 ------------------------------------------------------------------
$fail = @($results | Where-Object { $_.status -eq 'FAIL' }).Count
$pass = @($results | Where-Object { $_.status -eq 'PASS' }).Count
$skip = @($results | Where-Object { $_.status -eq 'SKIP' }).Count

Write-Output ''
Write-Output ('{0,-8}{1,-40}{2,-8}{3}' -f 'ID', '检查项', '状态', '明细')
Write-Output ('-' * 100)
foreach ($r in $results) {
    Write-Output ('{0,-8}{1,-40}{2,-8}{3}' -f $r.id, $r.item, $r.status, $r.detail)
}
Write-Output ('-' * 100)
Write-Output "G5 预检：PASS=$pass FAIL=$fail SKIP=$skip"

$jsonPath = $env:G5_JSON
if (-not $jsonPath) { $jsonPath = 'reports/ci/g5-precheck.json' }
$dir = Split-Path -Parent $jsonPath
if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
@{
    timestamp_utc = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    pass = $pass; fail = $fail; skip = $skip
    results = $results
} | ConvertTo-Json -Depth 5 | Set-Content -Path $jsonPath -Encoding UTF8
Write-Output "证据：$jsonPath"

if ($fail -gt 0) { exit 1 }
exit 0
