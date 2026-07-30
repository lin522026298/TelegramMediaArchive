@echo off
set "SCRIPT=%~dp0scripts\启动OpenList.ps1"
if not exist "%SCRIPT%" set "SCRIPT=%~dp0..\scripts\启动OpenList.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT%"
