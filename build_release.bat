@echo off
setlocal
title Canon Scan Pro - Build release
cd /d "%~dp0"
echo Building Canon Scan Pro release package. This takes several minutes...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_release.ps1"
set RC=%ERRORLEVEL%
echo.
if %RC% NEQ 0 (
  echo Build FAILED. Details are in build_log.txt
) else (
  echo Build finished. Open the "release" folder.
)
pause
exit /b %RC%
