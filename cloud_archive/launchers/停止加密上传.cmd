@echo off
set "SCRIPT=%~dp0scripts\停止加密上传.ps1"
if not exist "%SCRIPT%" set "SCRIPT=%~dp0..\scripts\停止加密上传.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
if errorlevel 1 pause
