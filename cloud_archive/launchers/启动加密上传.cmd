@echo off
set "SCRIPT=%~dp0scripts\启动加密上传.ps1"
if not exist "%SCRIPT%" set "SCRIPT=%~dp0..\scripts\启动加密上传.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
if errorlevel 1 pause
