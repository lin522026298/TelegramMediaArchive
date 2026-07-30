@echo off
set "SCRIPT=%~dp0scripts\打开OpenList管理页.ps1"
if not exist "%SCRIPT%" set "SCRIPT=%~dp0..\scripts\打开OpenList管理页.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT%"
