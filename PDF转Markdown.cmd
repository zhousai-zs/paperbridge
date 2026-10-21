@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0convert.ps1" %*
pause
