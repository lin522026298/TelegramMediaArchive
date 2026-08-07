@echo off
set "SCRIPT=%~dp0scripts\停止连续归档.ps1"
if not exist "%SCRIPT%" set "SCRIPT=%~dp0..\scripts\停止连续归档.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
if errorlevel 1 pause
