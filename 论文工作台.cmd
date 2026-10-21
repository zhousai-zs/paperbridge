@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1
if not exist ".venv\Scripts\python.exe" (
  echo Please run setup.ps1 first to install PaperBridge.
  pause
  popd
  exit /b 1
)
".venv\Scripts\python.exe" -m paperbridge.cli ui %*
set "paperbridge_exit=%errorlevel%"
popd
if not "%paperbridge_exit%"=="0" pause
exit /b %paperbridge_exit%
