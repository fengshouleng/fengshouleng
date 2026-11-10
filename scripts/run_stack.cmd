@echo off
REM ============================================================================
REM run_stack.cmd - Windows launcher for run_stack.ps1
REM
REM Double-clicking a .ps1 can fail with a Windows "package registration" error
REM (see scripts/README.md). This wrapper always calls a real PowerShell with an
REM explicit execution policy, which avoids that OS launch path.
REM
REM Usage:
REM   scripts\run_stack.cmd
REM   scripts\run_stack.cmd -ResetDb
REM   scripts\run_stack.cmd -ApiPort 8001 -WebPort 5174
REM
REM Set NO_PAUSE=1 to skip the final pause (automation).
REM ============================================================================
setlocal

set "SCRIPT=%~dp0run_stack.ps1"
if not exist "%SCRIPT%" (
  echo [ERROR] run_stack.ps1 not found next to this wrapper.
  exit /b 2
)

where pwsh >nul 2>nul
if %errorlevel%==0 (
  pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
) else (
  echo [INFO] pwsh 7 not found; using Windows PowerShell 5.1.
  powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
)

set "RC=%errorlevel%"
echo.
echo Exit code: %RC%
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
