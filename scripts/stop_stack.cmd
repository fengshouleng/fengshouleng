@echo off
REM ============================================================================
REM stop_stack.cmd - Windows launcher for stop_stack.ps1
REM Set NO_PAUSE=1 to skip the final pause (automation).
REM ============================================================================
setlocal

set "SCRIPT=%~dp0stop_stack.ps1"
if not exist "%SCRIPT%" (
  echo [ERROR] stop_stack.ps1 not found next to this wrapper.
  exit /b 2
)

where pwsh >nul 2>nul
if %errorlevel%==0 (
  pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
) else (
  powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
)

set "RC=%errorlevel%"
echo.
echo Exit code: %RC%
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
