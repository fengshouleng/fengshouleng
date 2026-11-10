# scripts/branch_protection.ps1
# main 分支保护（P-005）：先打印现状，再按需开启 required status checks。
#
# 需要具备 Administration: write 的 token（细粒度 PAT 要勾 Repository permissions → Administration: Read and write，
# 或使用经典 PAT 的 repo scope）。没有该权限时 GitHub 返回 403 —— 这是权限问题，不是脚本问题。
#
# 用法：
#   $env:GH_ADMIN_TOKEN = '<admin token>'
#   pwsh -File scripts/branch_protection.ps1                        # 只看现状
#   pwsh -File scripts/branch_protection.ps1 -Apply                 # 开启保护（required: CI 汇总门）
#   pwsh -File scripts/branch_protection.ps1 -Apply -Checks 'CI 汇总门','backend (ruff / mypy / pytest)'
#
# 证据：每次运行把 GET/PUT 的结果写入 reports/ci/branch-protection-<UTC>.json（该目录不入库）。
param(
    [switch]$Apply,
    [string[]]$Checks = @('CI 汇总门'),
    [string]$Repo = 'fengshouleng/fengshouleng',
    [string]$Branch = 'main'
)

$ErrorActionPreference = 'Stop'
$token = $env:GH_ADMIN_TOKEN
if (-not $token) {
    Write-Error '缺少 $env:GH_ADMIN_TOKEN（需要 Administration: write 权限）'
    exit 2
}

$headers = @{
    Authorization          = "Bearer $token"
    'User-Agent'           = 'dsh-agent'
    Accept                 = 'application/vnd.github+json'
}
$api = "https://api.github.com/repos/$Repo"
$protection = "$api/branches/$Branch/protection"
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$outDir = Join-Path (Join-Path $PSScriptRoot '..') 'reports/ci'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$evidence = Join-Path $outDir "branch-protection-$stamp.json"

$report = [ordered]@{
    timestamp_utc = $stamp
    repo          = $Repo
    branch        = $Branch
    apply         = [bool]$Apply
    checks        = $Checks
    before        = $null
    put           = $null
    errors        = @()
}

function Invoke-GhApi {
    param([string]$Method, [string]$Uri, [object]$Body)
    $params = @{ Uri = $Uri; Headers = $headers; Method = $Method; TimeoutSec = 60 }
    if ($null -ne $Body) {
        $params.Body = ($Body | ConvertTo-Json -Depth 8)
        $params.ContentType = 'application/json; charset=utf-8'
    }
    return Invoke-RestMethod @params
}

# --- 1) 现状 -----------------------------------------------------------------
try {
    $current = Invoke-GhApi 'GET' $protection $null
    $report.before = @{
        contexts = @($current.required_status_checks.contexts)
        strict   = $current.required_status_checks.strict
        enforce_admins = $current.enforce_admins.enabled
    }
    Write-Output "现状：contexts=$($report.before.contexts -join ',') strict=$($report.before.strict) enforce_admins=$($report.before.enforce_admins)"
} catch {
    $status = $_.Exception.Response.StatusCode.value__
    Write-Output "读取保护规则失败：HTTP $status（403=token 无 Administration 权限；404=当前未开启保护）"
    $report.errors += "GET protection -> $status"
}

# --- 2) 开启（可选） ---------------------------------------------------------
if ($Apply) {
    $payload = @{
        required_status_checks        = @{ strict = $true; contexts = $Checks }
        enforce_admins                = $true
        required_pull_request_reviews = @{ required_approving_review_count = 0 }
        restrictions                  = $null
    }
    try {
        $result = Invoke-GhApi 'PUT' $protection $payload
        $report.put = @{
            contexts = @($result.required_status_checks.contexts)
            strict   = $result.required_status_checks.strict
            enforce_admins = $result.enforce_admins.enabled
        }
        Write-Output "已开启：contexts=$($report.put.contexts -join ',') strict=$($report.put.strict) enforce_admins=$($report.put.enforce_admins)"
    } catch {
        $status = $_.Exception.Response.StatusCode.value__
        Write-Output "开启失败：HTTP $status（403 说明 token 缺 Administration: write，请改用仓库管理员网页设置，见 docs/77 §3.1）"
        $report.errors += "PUT protection -> $status"
    }
}

$report | ConvertTo-Json -Depth 6 | Set-Content -Path $evidence -Encoding UTF8
Write-Output "证据：$evidence"
if ($report.errors.Count -gt 0) { exit 1 }
exit 0
