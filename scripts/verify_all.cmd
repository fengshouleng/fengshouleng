@echo off
REM ============================================================================
REM verify_all.cmd - Windows launcher for verify_all.ps1
REM
REM Why this wrapper:
REM   Double-clicking a .ps1 file can fail with a Windows "package registration"
REM   error dialog depending on the .ps1 file association / packaged PowerShell.
REM   This wrapper always calls a real PowerShell executable with an explicit
REM   execution policy, which avoids that OS-level launch path entirely.
REM
REM Usage (from cmd / Explorer / CI):
REM   scripts\verify_all.cmd                      -> all basic checks
REM   scripts\verify_all.cmd -WithSecurity        -> extra security suite
REM   scripts\verify_all.cmd -WithE2E -WithPerf   -> extra E2E + perf
REM   scripts\verify_all.cmd -Only contract       -> only contract checks
REM
REM Exit code = number of failed steps (0 = all green).
REM Set NO_PAUSE=1 to skip the final pause (for automation).
REM ============================================================================
setlocal

set "SCRIPT=%~dp0verify_all.ps1"
if not exist "%SCRIPT%" (
  echo [ERROR] verify_all.ps1 not found next to this wrapper.
  exit /b 2
)

where pwsh >nul 2>nul
if %errorlevel%==0 (
  pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
) else (
  echo [WARN] pwsh 7 not found; falling back to Windows PowerShell 5.1.
  powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
)

set "RC=%errorlevel%"
echo.
echo Exit code: %RC%
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
