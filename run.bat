@echo off
REM Stop any running Fraud360 control-plane services and start fresh copies of all of
REM them (tenant, branding, config, ingestion, notification, decision, analytics,
REM gateway). Thin wrapper around run_local.ps1, which does the actual stop-then-start -
REM see that script for prerequisites (venv, Postgres, DB roles) and the service list.
REM
REM The ISO 8583 gateway (services/iso8583_gateway) is deliberately NOT started here -
REM it requires a machine credential and a fail-open/closed decision that no environment
REM has configured yet (see docs/ISO8583_LANE_A_SCOPING.md). Run it separately once that
REM onboarding is done.
REM
REM Ctrl+C stops every service this window started.

setlocal
cd /d "%~dp0"

echo Fraud360 control plane - stopping any running services and starting fresh...
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_local.ps1"

set EXITCODE=%ERRORLEVEL%
echo.
echo Fraud360 control plane stopped (exit code %EXITCODE%).
endlocal
exit /b %EXITCODE%
