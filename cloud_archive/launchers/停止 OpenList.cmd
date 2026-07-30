@echo off
set "SCRIPT=%~dp0scripts\停止OpenList.ps1"
if not exist "%SCRIPT%" set "SCRIPT=%~dp0..\scripts\停止OpenList.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT%"
