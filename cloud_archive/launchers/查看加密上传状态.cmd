@echo off
set "SCRIPT=%~dp0scripts\查看加密上传状态.ps1"
if not exist "%SCRIPT%" set "SCRIPT=%~dp0..\scripts\查看加密上传状态.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
pause
